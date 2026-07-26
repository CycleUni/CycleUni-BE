import logging
import uuid
from django.utils.translation import gettext_lazy as _

from django.db.models import Count
from rest_framework import status, views
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.cache import cache
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone

from accounts.serializers import RegisterSerializer, UserSerializer
from accounts.services import (
    issue_tokens,
    verify_and_revoke_refresh_token,
    revoke_all_tokens_for_user,
    get_grace_tokens,
    store_grace_tokens,
    email_already_used,
)
from accounts.models import School
from core.i18n import resolve_language
from core.models import Category
from listings.serializers import ListingSerializer
from subscriptions.models import Subscription, subscriptions_with_new_listings_count
from subscriptions.serializers import SubscriptionSerializer

logger = logging.getLogger(__name__)

User = get_user_model()


def _send_verification_email(subject, message, recipient_email, log_context):
    """Best-effort send: the verification token is already cached by the
    caller before this runs, so a transient Mailjet/SMTP failure shouldn't
    fail the whole request — log for ops visibility and degrade gracefully.

    Never logs `message` (it embeds the verification/activation token — a
    bearer credential good for 24h) or `recipient_email` (PII) — only the
    caller-supplied context label and, on failure, the exception.
    """
    try:
        from django.core.mail import EmailMessage
        msg = EmailMessage(
            subject=subject,
            body=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[recipient_email],
        )
        msg.send(fail_silently=False)
        if hasattr(msg, 'anymail_status'):
            logger.debug("Sent verification email (%s), anymail status=%s", log_context, getattr(msg.anymail_status, 'status', None))
    except Exception:
        logger.exception("Failed to send verification email (%s)", log_context)


class RegisterView(views.APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'register'

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()

            # Account is created inactive (see RegisterSerializer.create) —
            # this link is what activates it. Login is blocked until then
            # (LoginView checks is_active).
            verify_token = str(uuid.uuid4())
            cache.set(f"register-verify:{verify_token}", {"user_id": user.id}, timeout=86400)
            verify_link = f"{settings.FRONTEND_URL}/verify?token={verify_token}&type=register"

            lang = resolve_language(request)
            if lang == 'zh-TW':
                subject = 'CycleUni 帳號啟用信'
                message = f'感謝您註冊 CycleUni！請點擊以下連結以啟用您的帳號：\n\n{verify_link}\n\n如果您沒有註冊此帳號，請忽略這封信件。'
            else:
                subject = 'CycleUni Account Activation'
                message = f'Thanks for signing up for CycleUni! Click the link below to activate your account:\n\n{verify_link}\n\nIf you did not sign up for this account, please ignore this email.'

            name = f"{user.last_name}{user.first_name}".strip() or "User"
            recipient = f'"{name}" <{user.email}>'
            _send_verification_email(subject, message, recipient, f"registration for user {user.id}")

            return Response({"code": "auth.registerSuccess"}, status=status.HTTP_201_CREATED)
        return Response({"error": {"code": "auth.errValidation", "fields": serializer.errors}}, status=status.HTTP_400_BAD_REQUEST)


class RequestEduVerificationView(views.APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'verify-request'

    def post(self, request):
        edu_email = request.data.get('edu_email')
        if not edu_email or not isinstance(edu_email, str) or not edu_email.endswith('.edu.tw'):
            return Response({"error": {"code": "acct.errEduEmail"}}, status=status.HTTP_400_BAD_REQUEST)

        # Ensure the school is supported by checking if it exists in the database
        edu_email = edu_email.strip().lower()
        domain = edu_email.split('@')[-1]
        
        parts = domain.split('.')
        school_exists = False
        for i in range(len(parts) - 1):
            sub_domain = '.'.join(parts[i:])
            if School.objects.filter(email_domain__iexact=sub_domain).exists():
                school_exists = True
                break
                
        if not school_exists:
            return Response({"error": {"code": "acct.errSchoolNotSupported"}}, status=status.HTTP_400_BAD_REQUEST)

        # Check if email is already in use by someone else as edu_email or email
        if email_already_used(edu_email, request.user.id):
            return Response({"error": {"code": "acct.errEmailTaken"}}, status=status.HTTP_400_BAD_REQUEST)

        verify_token = str(uuid.uuid4())
        # Cache token with user_id and edu_email for 24 hours
        cache.set(f"verify:{verify_token}", {"user_id": request.user.id, "edu_email": edu_email}, timeout=86400)

        verify_link = f"{settings.FRONTEND_URL}/verify?token={verify_token}&type=edu"

        lang = resolve_language(request)
        if lang == 'zh-TW':
            subject = 'CycleUni 學生信箱驗證'
            message = f'請點擊以下連結以驗證您的學生信箱：\n\n{verify_link}\n\n如果您沒有請求此驗證，請忽略這封信件。'
        else:
            subject = 'CycleUni Student Email Verification'
            message = f'Click the link below to verify your student email:\n\n{verify_link}\n\nIf you did not request this verification, please ignore this email.'

        name = f"{request.user.last_name}{request.user.first_name}".strip() or "User"
        recipient = f'"{name}" <{edu_email}>'
        _send_verification_email(subject, message, recipient, f"edu verification for user {request.user.id}")

        return Response({"code": "acct.sentVerification"}, status=status.HTTP_200_OK)


class AutoVerifyEduEmailView(views.APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'verify-request'

    def post(self, request):
        user = request.user
        if user.verified_at:
            return Response({"code": "acct.verifySuccess"})
            
        email = user.email.strip().lower()
        if not email.endswith('.edu.tw'):
            return Response({"error": {"code": "acct.errEduEmail"}}, status=status.HTTP_400_BAD_REQUEST)
            
        domain = email.split('@')[-1]
        parts = domain.split('.')
        school = None
        for i in range(len(parts) - 1):
            sub_domain = '.'.join(parts[i:])
            try:
                school = School.objects.get(email_domain__iexact=sub_domain)
                break
            except School.DoesNotExist:
                continue
                
        if not school:
            return Response({"error": {"code": "acct.errSchoolNotSupported"}}, status=status.HTTP_400_BAD_REQUEST)
            
        # Check if email is already in use by someone else as edu_email
        if email_already_used(email, user.id):
            return Response({"error": {"code": "acct.errEmailTaken"}}, status=status.HTTP_400_BAD_REQUEST)

        user.edu_email = email
        user.verified_at = timezone.now()
        user.school = school
        user.save(update_fields=['edu_email', 'verified_at', 'school'])
        
        return Response({"code": "acct.verifySuccess"})


class VerifyEmailView(views.APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        token = request.data.get('token')
        if not token:
            return Response({"error": {"code": "auth.errMissingToken"}}, status=status.HTTP_400_BAD_REQUEST)

        token_data = cache.get(f"verify:{token}")
        if not token_data or not isinstance(token_data, dict):
            return Response({"error": {"code": "auth.errInvalidToken"}}, status=status.HTTP_400_BAD_REQUEST)

        user_id = token_data.get('user_id')
        edu_email = token_data.get('edu_email')

        try:
            user = User.objects.get(id=user_id)
            user.verified_at = timezone.now()
            user.edu_email = edu_email
            
            domain = edu_email.split('@')[-1].lower() if edu_email and '@' in edu_email else None
            if domain:
                from accounts.models import School
                parts = domain.split('.')
                school = None
                for i in range(len(parts) - 1):
                    sub_domain = '.'.join(parts[i:])
                    try:
                        school = School.objects.get(email_domain__iexact=sub_domain)
                        break
                    except School.DoesNotExist:
                        continue
                if school:
                    user.school = school

            user.save(update_fields=['verified_at', 'edu_email', 'school'])
            cache.delete(f"verify:{token}")
            return Response({"code": "acct.verifySuccess"})
        except User.DoesNotExist:
            return Response({"error": {"code": "auth.errUserNotFound"}}, status=status.HTTP_404_NOT_FOUND)


class VerifyRegistrationView(views.APIView):
    """Activates a just-registered account (see RegisterSerializer.create,
    is_active=False) from the link mailed by RegisterView. Unlike
    VerifyEmailView (edu-email binding, done from an already-logged-in
    session), the caller here has no session yet — so on success this
    issues JWTs directly, landing the user logged in."""
    permission_classes = [AllowAny]

    def post(self, request):
        token = request.data.get('token')
        if not token:
            return Response({"error": {"code": "auth.errMissingToken"}}, status=status.HTTP_400_BAD_REQUEST)

        token_data = cache.get(f"register-verify:{token}")
        if not token_data or not isinstance(token_data, dict):
            return Response({"error": {"code": "auth.errInvalidToken"}}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user = User.objects.get(id=token_data.get('user_id'))
        except User.DoesNotExist:
            return Response({"error": {"code": "auth.errUserNotFound"}}, status=status.HTTP_404_NOT_FOUND)

        if not user.is_active:
            user.is_active = True
            user.save(update_fields=['is_active'])
        cache.delete(f"register-verify:{token}")

        tokens = issue_tokens(user)
        return Response(tokens)


class LoginView(views.APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'login'

    def post(self, request):
        email = request.data.get('email')
        password = request.data.get('password')

        if not email or not password:
            return Response({"error": {"code": "auth.errValidation"}}, status=status.HTTP_400_BAD_REQUEST)

        invalid_credentials = Response({"error": {"code": "auth.errInvalidCredentials"}}, status=status.HTTP_401_UNAUTHORIZED)

        # Case-insensitive: registration only lowercases the domain part
        # (Django's normalize_email), so an account registered as
        # "User@example.com" would otherwise only match an exact-case retype
        # of that same address — nobody expects email lookups to be
        # case-sensitive. .first() rather than .get() so the pre-existing
        # (not newly introduced) possibility of two accounts differing only
        # by case can't turn into a 500.
        user = User.objects.filter(email__iexact=email).first()
        if not user:
            # Run a dummy hash so response timing does not reveal whether the account exists
            User().set_password(password)
            return invalid_credentials

        if not user.check_password(password):
            return invalid_credentials
        if not user.is_active:
            return Response({"error": {"code": "auth.errAccountDisabled"}}, status=status.HTTP_403_FORBIDDEN)

        tokens = issue_tokens(user)
        return Response(tokens)


class RefreshTokenView(views.APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'refresh_token'

    def post(self, request):
        refresh_token = request.data.get('refresh')
        if not refresh_token:
            return Response({"error": {"code": "auth.errValidation"}}, status=status.HTTP_400_BAD_REQUEST)

        try:
            token = RefreshToken(refresh_token)
            jti = token['jti']
            user_id = token['user_id']

            # Already rotated (concurrent refresh from multiple tabs, or a
            # delayed replay of an old-but-still-within-lifetime token):
            # return the same pair it was rotated into rather than erroring.
            grace_tokens = get_grace_tokens(jti, user_id)
            if grace_tokens:
                return Response(grace_tokens)

            # Whitelist check; revokes the old jti on success. A bare "not
            # found" (cache miss, eviction, dev-server restart) fails just
            # this one refresh — it no longer nukes every other session; see
            # verify_and_revoke_refresh_token's docstring for why that
            # escalation was the actual bug behind frequent unwanted logouts.
            if not verify_and_revoke_refresh_token(jti, user_id):
                return Response({"error": {"code": "auth.errTokenRevoked"}}, status=status.HTTP_401_UNAUTHORIZED)

            user = User.objects.get(id=user_id)
            if not user.is_active:
                return Response({"error": {"code": "auth.errAccountDisabled"}}, status=status.HTTP_403_FORBIDDEN)

            tokens = issue_tokens(user)
            store_grace_tokens(jti, user_id, tokens)
            return Response(tokens)
        except (TokenError, User.DoesNotExist):
            return Response({"error": {"code": "auth.errInvalidToken"}}, status=status.HTTP_401_UNAUTHORIZED)


class GoogleLoginView(views.APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'login'

    def post(self, request):
        credential = request.data.get('credential')
        if not credential:
            return Response({"error": {"code": "auth.errMissingCredential"}}, status=status.HTTP_400_BAD_REQUEST)

        from allauth.socialaccount.adapter import get_adapter
        from allauth.socialaccount.providers.google.provider import GoogleProvider
        from allauth.socialaccount.providers.google.views import _verify_and_decode
        from allauth.socialaccount.models import SocialAccount

        try:
            provider = get_adapter().get_provider(request, GoogleProvider.id)
            app = provider.app
        except Exception:
            return Response({"error": {"code": "auth.errProviderNotConfigured"}}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        try:
            # allauth will securely verify the ID token signature, issuer, and audience (client_id)
            # We monkey-patch PyJWT momentarily to add 5 seconds of leeway for clock skew (iat)
            import jwt
            _original_jwt_decode = jwt.decode
            
            def _jwt_decode_with_leeway(*args, **kwargs):
                kwargs.setdefault('leeway', 5)
                return _original_jwt_decode(*args, **kwargs)
                
            jwt.decode = _jwt_decode_with_leeway
            try:
                idinfo = _verify_and_decode(app, credential, verify_signature=True)
            finally:
                jwt.decode = _original_jwt_decode
                
            email = idinfo.get('email')
            if not email:
                return Response({"error": {"code": "auth.errNoEmail"}}, status=status.HTTP_400_BAD_REQUEST)
            
            uid = idinfo.get('sub')
            first_name = idinfo.get('given_name', '')
            last_name = idinfo.get('family_name', '')
            
            avatar_url = idinfo.get('picture', '')

            # Find or create user
            user, created = User.objects.get_or_create(
                email=email,
                defaults={
                    'first_name': first_name,
                    'last_name': last_name,
                    'avatar_url': avatar_url,
                }
            )
            if not created and avatar_url and user.avatar_url != avatar_url:
                user.avatar_url = avatar_url
                user.save(update_fields=['avatar_url'])
            if created:
                user.set_unusable_password()
                user.save()
            
            # If the user registered normally but hasn't activated their account (is_active=False),
            # Google login proves email ownership, so we activate them automatically.
            if not user.is_active:
                user.is_active = True
                user.save(update_fields=['is_active'])

            # Automatically bind and verify edu email if the Google email is a supported .edu.tw address
            if email.endswith('.edu.tw') and (not user.verified_at or user.edu_email != email):
                domain = email.split('@')[-1].lower()
                parts = domain.split('.')
                school = None
                for i in range(len(parts) - 1):
                    sub_domain = '.'.join(parts[i:])
                    try:
                        school = School.objects.get(email_domain__iexact=sub_domain)
                        break
                    except School.DoesNotExist:
                        continue
                if school:
                    user.edu_email = email
                    user.verified_at = timezone.now()
                    user.school = school
                    user.save(update_fields=['edu_email', 'verified_at', 'school'])

            # Link with SocialAccount
            SocialAccount.objects.get_or_create(
                user=user,
                provider=GoogleProvider.id,
                uid=uid,
                defaults={
                    'extra_data': idinfo
                }
            )

            # Issued the same way as password login, so the refresh token
            # is recorded in the JWT whitelist (jwt:rt:*/jwt:user:*) — a
            # token minted directly via RefreshToken.for_user() would never
            # be in that whitelist, making it both unrefreshable (rotation
            # checks the whitelist) and invisible to revoke_all_tokens_for_user.
            tokens = issue_tokens(user)

            return Response({
                "access": tokens['access'],
                "refresh": tokens['refresh'],
                "user_id": user.id,
                "user": {
                    "is_verified": user.is_verified()
                }
            }, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.error(f"Google token verification failed: {e}", exc_info=True)
            return Response({"error": {"code": "auth.errInvalidToken"}}, status=status.HTTP_401_UNAUTHORIZED)




class LogoutView(views.APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        refresh_token = request.data.get('refresh')
        all_devices = request.data.get('all_devices', False)

        user_id = request.user.id

        if all_devices:
            revoke_all_tokens_for_user(user_id)
        elif refresh_token:
            try:
                token = RefreshToken(refresh_token)
                jti = token['jti']
                # The whitelist stores/compares user_id as a string (matching
                # simplejwt's own str(user.id) claim, see issue_tokens) —
                # request.user.id is a real int, so it must be cast here or
                # this never matches and the token silently stays whitelisted
                # despite the user asking to log out.
                verify_and_revoke_refresh_token(jti, str(user_id))
            except TokenError:
                pass  # Ignore invalid tokens; we are logging out anyway

        return Response({"code": "auth.logoutSuccess"}, status=status.HTTP_200_OK)


class AuthConfigView(views.APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        from django.conf import settings
        return Response({
            "google_client_id": getattr(settings, "GOOGLE_CLIENT_ID", "")
        })


class MyProfileView(views.APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        serializer = UserSerializer(user, context={'request': request})
        data = serializer.data

        # Related data the frontend My Account page needs
        my_listings = user.listings.select_related('book', 'seller', 'school').order_by('-created_at')
        
        q = request.query_params.get('q', '').strip()
        if q:
            from django.db.models import Q
            my_listings = my_listings.filter(
                Q(book__title__icontains=q) | 
                Q(book__authors__icontains=q) | 
                Q(book__isbn13__icontains=q)
            )
            
        from rest_framework.pagination import PageNumberPagination
        paginator = PageNumberPagination()
        page = paginator.paginate_queryset(my_listings, request)
        
        if page is not None:
            data['myListings'] = {
                'count': paginator.page.paginator.count,
                'next': paginator.get_next_link(),
                'previous': paginator.get_previous_link(),
                'results': ListingSerializer(page, many=True, context={'request': request}).data
            }
        else:
            data['myListings'] = {
                'count': my_listings.count(),
                'next': None,
                'previous': None,
                'results': ListingSerializer(my_listings, many=True, context={'request': request}).data
            }

        my_subs = subscriptions_with_new_listings_count(user.subscriptions.all())
        data['mySubscriptions'] = SubscriptionSerializer(my_subs, many=True).data

        return Response(data)

    def patch(self, request):
        user = request.user
        first_name = request.data.get('first_name')
        last_name = request.data.get('last_name')
        email = request.data.get('email')
        
        if first_name is not None and not isinstance(first_name, str):
            return Response({"first_name": [_("Invalid value.")]}, status=status.HTTP_400_BAD_REQUEST)
        if last_name is not None and not isinstance(last_name, str):
            return Response({"last_name": [_("Invalid value.")]}, status=status.HTTP_400_BAD_REQUEST)

        updated_fields = []
        if first_name is not None:
            user.first_name = first_name.strip()
            updated_fields.append('first_name')
        if last_name is not None:
            user.last_name = last_name.strip()
            updated_fields.append('last_name')
        if email is not None:
            email = email.strip().lower()
            if email != user.email:
                if user.socialaccount_set.filter(provider='google').exists():
                    return Response({"email": [_("You cannot change the email of a Google-linked account.")]}, status=status.HTTP_400_BAD_REQUEST)
                if User.objects.filter(email__iexact=email).exclude(pk=user.pk).exists():
                    return Response({"email": [_("Email is already in use.")]}, status=status.HTTP_400_BAD_REQUEST)
                # A login email matching a supported school's domain is what
                # AutoVerifyEduEmailView trusts to grant verified-student
                # status with no further proof — so this endpoint must never
                # let that value become one the user hasn't actually proven
                # ownership of. Legitimate school-email logins are still
                # possible (set at registration, proven via the activation
                # link), just not by editing it in afterward.
                new_domain = email.split('@')[-1] if '@' in email else ''
                if School.objects.filter(email_domain__iexact=new_domain).exists():
                    return Response({"error": {"code": "acct.errEduEmailChangeNotAllowed"}}, status=status.HTTP_400_BAD_REQUEST)
                user.email = email
                updated_fields.append('email')
            
        if updated_fields:
            user.save(update_fields=updated_fields)
            
        serializer = UserSerializer(user, context={'request': request})
        return Response(serializer.data)

    def delete(self, request):
        user = request.user
        user.delete()
        return Response({"code": "acct.deleted"}, status=status.HTTP_204_NO_CONTENT)


class PublicUserProfileView(views.APIView):
    permission_classes = [AllowAny]

    def get(self, request, pk):
        try:
            user = User.objects.get(pk=pk)
        except User.DoesNotExist:
            return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)
            
        from accounts.serializers import PublicUserProfileSerializer
        serializer = PublicUserProfileSerializer(user, context={'request': request})
        return Response(serializer.data)

class ChangePasswordView(views.APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'password_change'

    def post(self, request):
        user = request.user
        old_password = request.data.get("old_password")
        new_password = request.data.get("new_password")

        if not new_password:
            return Response({"error": {"code": "auth.errValidation"}}, status=status.HTTP_400_BAD_REQUEST)

        if user.has_usable_password():
            if not old_password:
                return Response({"error": {"code": "auth.errValidation"}}, status=status.HTTP_400_BAD_REQUEST)
            if not user.check_password(old_password):
                return Response({"old_password": [_("Incorrect password.")]}, status=status.HTTP_400_BAD_REQUEST)

        user.set_password(new_password)
        user.save()
        return Response({"code": "acct.passwordUpdated"})


class RequestPasswordResetView(views.APIView):
    """Logged-out "forgot password" entry point: emails a reset link if the
    address belongs to an account. Always returns the same success response
    regardless of whether the email exists, so this can't be used to probe
    which addresses are registered."""
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'password-reset-request'

    def post(self, request):
        email = request.data.get('email')
        if not email or not isinstance(email, str):
            return Response({"error": {"code": "auth.errValidation"}}, status=status.HTTP_400_BAD_REQUEST)
        email = email.strip()

        # Case-insensitive, like LoginView — registration only lowercases the
        # domain part, so an account registered as "User@example.com" would
        # otherwise silently fail to match a plain-lowercase retype here and
        # this view would (by design, to avoid leaking which emails are
        # registered) return the same generic success response with no email
        # ever sent. That mismatch, not a Mailjet/deliverability problem, was
        # the actual bug behind "forgot password" appearing to do nothing.
        user = User.objects.filter(email__iexact=email).first()
        if not user:
            return Response({"code": "acct.passwordResetSent"})

        # A Google-linked account has no usable password to reset — sending
        # a reset link would just confuse someone who should use "Continue
        # with Google" instead. Still returns the generic success response.
        if user.socialaccount_set.filter(provider='google').exists():
            return Response({"code": "acct.passwordResetSent"})

        reset_token = str(uuid.uuid4())
        cache.set(f"password-reset:{reset_token}", {"user_id": user.id}, timeout=3600)
        reset_link = f"{settings.FRONTEND_URL}/reset-password?token={reset_token}"

        lang = resolve_language(request)
        if lang == 'zh-TW':
            subject = 'CycleUni 密碼重設'
            message = f'請點擊以下連結以重設您的密碼（1 小時內有效）：\n\n{reset_link}\n\n如果您沒有請求重設密碼，請忽略這封信件，您的密碼不會被更動。'
        else:
            subject = 'CycleUni Password Reset'
            message = f'Click the link below to reset your password (valid for 1 hour):\n\n{reset_link}\n\nIf you did not request this, you can ignore this email — your password will not be changed.'

        _send_verification_email(subject, message, user.email, f"password reset for user {user.id}")

        return Response({"code": "acct.passwordResetSent"})


class ConfirmPasswordResetView(views.APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        token = request.data.get('token')
        new_password = request.data.get('new_password')
        if not token:
            return Response({"error": {"code": "auth.errMissingToken"}}, status=status.HTTP_400_BAD_REQUEST)
        if not new_password:
            return Response({"error": {"code": "auth.errValidation"}}, status=status.HTTP_400_BAD_REQUEST)

        token_data = cache.get(f"password-reset:{token}")
        if not token_data or not isinstance(token_data, dict):
            return Response({"error": {"code": "auth.errInvalidToken"}}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user = User.objects.get(id=token_data.get('user_id'))
        except User.DoesNotExist:
            return Response({"error": {"code": "auth.errUserNotFound"}}, status=status.HTTP_404_NOT_FOUND)

        try:
            validate_password(new_password, user=user)
        except DjangoValidationError as e:
            return Response({"error": {"code": "auth.errValidation", "fields": e.messages}}, status=status.HTTP_400_BAD_REQUEST)

        user.set_password(new_password)
        user.save(update_fields=['password'])
        cache.delete(f"password-reset:{token}")

        # A password reset is exactly the moment any existing session might
        # be the compromised credential this reset is meant to fix — don't
        # leave old refresh tokens (any device, any tab) still valid.
        revoke_all_tokens_for_user(user.id)

        return Response({"code": "acct.passwordResetSuccess"})


class UnbindEduEmailView(views.APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        user.edu_email = ""
        user.verified_at = None
        user.last_reverified_at = None
        user.school = None
        user.save()
        return Response({"code": "acct.unbindSuccess"})


# The only two languages the frontend ever requests (see
# CycleUni-FE/src/app/core/i18n.service.ts) — kept in sync manually since
# `translations` fields accept arbitrary language tags, but the UI itself
# only ever renders these two, so only these two cache entries can exist.
HOME_STATIC_CACHE_LANGUAGES = ('en', 'zh-TW')


def invalidate_home_static_cache():
    """Bust HomeMetadataView's 24h schools/categories cache. Call this
    anywhere a School or Category row is created, updated, deleted, or
    bulk-imported (adminapi.views) — otherwise admin changes don't show up
    on the homepage for up to 24 hours."""
    for lang in HOME_STATIC_CACHE_LANGUAGES:
        cache.delete(f"home_static_{lang}")


class HomeMetadataView(views.APIView):
    """
    Home page metadata (schools, categories, and most wanted books).

    This view depends on `accounts.School` and `subscriptions.Subscription`. It is placed in
    the `accounts` app instead of `core` to avoid circular dependencies and keep `core`
    as an independent module. The URL remains `/api/v1/core/metadata/`.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        lang = resolve_language(request)

        # 1. Static data (Schools & Categories) cached for 24 hours
        static_cache_key = f"home_static_{lang}"
        static_data = cache.get(static_cache_key)

        if not static_data:
            schools = [
                {
                    'id': school.id,
                    'name': school.name,
                    'display_name': school.localized_name(lang),
                    'email_domain': school.email_domain,
                }
                for school in School.objects.all()
            ]

            categories = [
                category.localized(lang)
                for category in Category.objects.filter(is_active=True)
            ]

            static_data = {
                "schools": schools,
                "categories": categories,
            }
            cache.set(static_cache_key, static_data, timeout=86400)  # 24 hours

        # 2. Dynamic data (Most Wanted) cached for 5 minutes
        waitlist_cache_key = "home_waitlist"
        waitlist = cache.get(waitlist_cache_key)

        if waitlist is None:
            top_books = Subscription.objects.values('book__title').annotate(count=Count('user')).order_by('-count')[:7]
            waitlist = [{"title": item['book__title'], "count": item['count']} for item in top_books]
            cache.set(waitlist_cache_key, waitlist, timeout=300)  # 5 minutes

        # 3. Combine and return
        response_data = {
            "lang": lang,
            "schools": static_data["schools"],
            "categories": static_data["categories"],
            "waitlist": waitlist
        }

        return Response(response_data)
