from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import Profile


class ProfileInline(admin.StackedInline):
    model = Profile
    can_delete = False
    verbose_name_plural = "Department"


User = get_user_model()
admin.site.unregister(User)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Standard user admin + department. Set the role with the 'Groups' field (auditor / employee)."""

    inlines = [ProfileInline]
    list_display = ("username", "first_name", "last_name", "is_superuser", "department", "role_groups")

    def get_inline_instances(self, request, obj=None):
        # On the "add user" page the profile is created automatically; edit the department afterwards.
        return super().get_inline_instances(request, obj) if obj else []

    @admin.display(description="Department")
    def department(self, obj):
        profile = getattr(obj, "profile", None)
        return profile.department if profile else ""

    @admin.display(description="Groups")
    def role_groups(self, obj):
        return ", ".join(obj.groups.values_list("name", flat=True))
