from .roles import ROLE_GROUPS


def create_role_groups(sender, **kwargs):
    from django.contrib.auth.models import Group

    for name in ROLE_GROUPS:
        Group.objects.get_or_create(name=name)


def create_profile(sender, instance, created, **kwargs):
    from .models import Profile

    if created:
        Profile.objects.get_or_create(user=instance)
