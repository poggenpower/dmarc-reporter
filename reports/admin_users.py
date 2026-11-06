ADMIN_GROUP_NAME = "Admin users"


def is_admin_user(user):
    return user.is_superuser or user.groups.filter(name=ADMIN_GROUP_NAME).exists()
