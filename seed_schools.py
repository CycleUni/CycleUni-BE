import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cycleuni.settings')
django.setup()

from django.conf import settings

if not settings.DEBUG:
    print(
        "ERROR: refusing to run seed_schools.py with DEBUG=False "
        "(this script must never run against production)."
    )
    sys.exit(1)

from accounts.models import School

# Canonical Chinese names with en translations
schools_data = [
    {"name": "國立台灣大學", "email_domain": "ntu.edu.tw", "en_name": "National Taiwan University"},
    {"name": "國立政治大學", "email_domain": "nccu.edu.tw", "en_name": "National Chengchi University"},
    {"name": "國立台灣師範大學", "email_domain": "ntnu.edu.tw", "en_name": "National Taiwan Normal University"},
    {"name": "國立成功大學", "email_domain": "ncku.edu.tw", "en_name": "National Cheng Kung University"},
    {"name": "國立清華大學", "email_domain": "nthu.edu.tw", "en_name": "National Tsing Hua University"},
]

for sd in schools_data:
    School.objects.update_or_create(
        email_domain=sd['email_domain'],
        defaults={
            "name": sd['name'],
            "translations": {"en": {"name": sd['en_name']}},
        },
    )

print("Schools seeded!")
