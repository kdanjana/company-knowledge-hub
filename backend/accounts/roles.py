ADMIN = "admin"  # manages user accounts (create / edit / deactivate / lock / unlock); no audit pages
AUDITOR = "auditor"
EMPLOYEE = "employee"
ROLE_GROUPS = (ADMIN, AUDITOR, EMPLOYEE)
# Roles an admin may give through the API. Admins are created only from the command line (create_admin).
ASSIGNABLE_ROLES = (EMPLOYEE, AUDITOR)

DEPARTMENTS = [
    "Customer Support",
    "Engineering",
    "Finance",
    "HR",
    "IT",
    "Legal",
    "Marketing",
    "Operations",
]


def is_auditor(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    return user.is_superuser or user.groups.filter(name=AUDITOR).exists()


def is_admin(user) -> bool:
    """Admins and superusers manage user accounts."""
    if not user or not user.is_authenticated:
        return False
    return user.is_superuser or user.groups.filter(name=ADMIN).exists()


def role_of(user) -> str:
    # A superuser counts as auditor here (keeps the audit pages) and also passes is_admin().
    if is_auditor(user):
        return AUDITOR
    if is_admin(user):
        return ADMIN
    return EMPLOYEE


def department_of(user) -> str:
    profile = getattr(user, "profile", None)  # missing profile raises an AttributeError subclass -> None
    return profile.department if profile else ""


def allowed_departments(user) -> list[str] | None:
    """None = may see every document (auditors). Otherwise the departments whose restricted
    documents this user may see (documents open to everyone are always visible)."""
    if is_auditor(user):
        return None
    department = department_of(user)
    return [department] if department else []
