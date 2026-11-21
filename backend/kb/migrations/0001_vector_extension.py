# Turns on the pgvector extension in Postgres before any vector column is created.
# The tables themselves are created by `python manage.py makemigrations kb` (README step 5).
from django.db import migrations
from pgvector.django import VectorExtension


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [VectorExtension()]
