from rest_framework.permissions import BasePermission

from .roles import is_admin, is_auditor


class IsAuditor(BasePermission):
    message = "Only auditors can use this part of the app."

    def has_permission(self, request, view) -> bool:
        return is_auditor(request.user)


class IsAdmin(BasePermission):
    message = "Only admins can manage user accounts."

    def has_permission(self, request, view) -> bool:
        return is_admin(request.user)
