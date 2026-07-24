from rest_framework import serializers
from .models import Report


class ReportListingSerializer(serializers.Serializer):
    """Lightweight nested serializer, exposes only the listing fields needed by the report page."""
    id = serializers.UUIDField(read_only=True)
    title = serializers.CharField(source='book.title', read_only=True)


class ReportReporterSerializer(serializers.Serializer):
    """Exposes only the minimal fields staff need to identify the reporter, to avoid leaking sensitive info."""
    id = serializers.UUIDField(read_only=True)
    email = serializers.EmailField(read_only=True)


class ReportCreateSerializer(serializers.ModelSerializer):
    """For creating a report. `reporter` is not writable by the client; the view sets it from request.user."""

    class Meta:
        model = Report
        fields = ('id', 'listing', 'reason', 'detail', 'status', 'created_at')
        read_only_fields = ('id', 'status', 'created_at')

    def validate_reason(self, value):
        valid_reasons = dict(Report.REASON_CHOICES)
        if value not in valid_reasons:
            raise serializers.ValidationError({"code": "moderation.errInvalidReason"})
        return value


class ReportSerializer(serializers.ModelSerializer):
    """For reading reports (user's own submitted reports / staff review list)."""

    listing = ReportListingSerializer(read_only=True)
    reporter = ReportReporterSerializer(read_only=True)

    class Meta:
        model = Report
        fields = ('id', 'listing', 'reporter', 'reason', 'detail', 'status', 'created_at')
        read_only_fields = fields


class ReportStatusUpdateSerializer(serializers.ModelSerializer):
    """Staff-only: only allows the valid transitions open -> actioned / open -> dismissed."""

    class Meta:
        model = Report
        fields = ('status',)

    def validate_status(self, value):
        valid_transitions = {
            'open': ['actioned', 'dismissed'],
            'actioned': [],
            'dismissed': [],
        }

        current_status = self.instance.status
        if value not in valid_transitions.get(current_status, []):
            raise serializers.ValidationError(f"Cannot transition from {current_status} to {value}.")

        return value
