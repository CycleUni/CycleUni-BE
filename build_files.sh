uv venv
uv pip install -r requirements.txt
uv run python manage.py migrate
uv run python manage.py collectstatic --noinput