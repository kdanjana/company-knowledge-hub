from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"
    verbose_name = "Users, roles and departments"

    def ready(self):
        from django.contrib.auth import get_user_model
        from django.db.models.signals import post_migrate, post_save

        from . import signals

        # Create the role groups after every migrate, and a profile for every new user.
        post_migrate.connect(signals.create_role_groups, sender=self)
        post_save.connect(signals.create_profile, sender=get_user_model())
