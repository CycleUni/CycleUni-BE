import django.db.models.deletion
import listings.models
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('accounts', '0001_initial'),
        ('catalog', '0001_initial'),
        ('core', '0002_seed_categories'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Listing',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('price', models.PositiveIntegerField()),
                ('condition', models.CharField(choices=[('new', '全新'), ('like_new', '近全新'), ('noted', '有筆記'), ('damaged', '有破損')], max_length=20)),
                ('private_note', models.TextField(blank=True)),
                ('description', models.TextField(blank=True)),
                ('photos', models.JSONField(default=list, help_text='S3 object keys 陣列')),
                ('status', models.CharField(choices=[('active', '上架中'), ('reserved', '預留中'), ('sold', '已售出'), ('removed', '已下架')], default='active', max_length=20)),
                ('delivery_methods', models.JSONField(default=listings.models.default_delivery)),
                ('payment_methods', models.JSONField(default=listings.models.default_payment)),
                ('course_name', models.CharField(blank=True, default='', max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('book', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='listings', to='catalog.book')),
                ('category', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='listings', to='core.category')),
                ('school', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='listings', to='accounts.school')),
                ('seller', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='listings', to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
