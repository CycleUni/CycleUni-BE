# Data migration: seed the homepage college categories. These previously
# only existed as manually-inserted rows in local dev databases with no
# migration behind them, so a fresh clone/CI database ended up with an empty
# category list (same "mock/empty data" problem the Category model itself
# was introduced to fix).

from django.db import migrations

CATEGORIES = [
    {
        'slug': 'management',
        'title': '商管學院',
        'description': '經濟、會計、企管',
        'en_title': 'College of Management',
        'en_description': 'Economics, Accounting, Business Administration',
    },
    {
        'slug': 'engineering',
        'title': '工學院',
        'description': '機械、土木、化工',
        'en_title': 'College of Engineering',
        'en_description': 'Mechanical, Civil, Chemical Engineering',
    },
    {
        'slug': 'science',
        'title': '理學院',
        'description': '數學、物理、化學',
        'en_title': 'College of Science',
        'en_description': 'Mathematics, Physics, Chemistry',
    },
    {
        'slug': 'liberal-arts',
        'title': '文學院',
        'description': '外文、中文、歷史',
        'en_title': 'College of Liberal Arts',
        'en_description': 'Foreign Languages, Chinese, History',
    },
    {
        'slug': 'medicine',
        'title': '醫學院',
        'description': '醫學、護理、藥學',
        'en_title': 'College of Medicine',
        'en_description': 'Medicine, Nursing, Pharmacy',
    },
    {
        'slug': 'eecs',
        'title': '電資學院',
        'description': '電機、資工',
        'en_title': 'College of EECS',
        'en_description': 'Electrical Engineering, Computer Science',
    },
    {
        'slug': 'law',
        'title': '法學院',
        'description': '法律學系',
        'en_title': 'College of Law',
        'en_description': 'Department of Law',
    },
    {
        'slug': 'social-sciences',
        'title': '社科院',
        'description': '政治、社會、社工',
        'en_title': 'College of Social Sciences',
        'en_description': 'Political Science, Sociology, Social Work',
    },
]


def forwards(apps, schema_editor):
    Category = apps.get_model('core', 'Category')
    for order, item in enumerate(CATEGORIES, start=1):
        Category.objects.update_or_create(
            slug=item['slug'],
            defaults={
                'title': item['title'],
                'description': item['description'],
                'translations': {
                    'en': {
                        'title': item['en_title'],
                        'description': item['en_description'],
                    }
                },
                'sort_order': order,
                'is_active': True,
            },
        )


def backwards(apps, schema_editor):
    Category = apps.get_model('core', 'Category')
    Category.objects.filter(slug__in=[item['slug'] for item in CATEGORIES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
