from django.conf import settings
from django.db import migrations

ADMIN_GROUP_NAME = "Admin users"


def create_admin_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    user_app, user_model = settings.AUTH_USER_MODEL.split(".")
    User = apps.get_model(user_app, user_model)
    group, _ = Group.objects.using(schema_editor.connection.alias).get_or_create(
        name=ADMIN_GROUP_NAME
    )
    existing_staff = User.objects.using(schema_editor.connection.alias).filter(is_staff=True)
    for user in existing_staff.iterator():
        group.user_set.add(user)


class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0004_importedmessage_sender_importedmessage_sent_at_and_more"),
        ("auth", "0012_alter_user_first_name_max_length"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(create_admin_group, migrations.RunPython.noop),
    ]
