from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import transaction
from django.db.models.signals import m2m_changed, post_migrate, post_save
from django.dispatch import receiver

from .admin_users import ADMIN_GROUP_NAME


@receiver(post_migrate)
def grant_admin_group_permissions(sender, **kwargs):
    if sender.label != "reports":
        return
    group = Group.objects.filter(name=ADMIN_GROUP_NAME).first()
    if group:
        group.permissions.set(group.permissions.model.objects.all())


@receiver(post_save, sender=get_user_model())
def make_first_user_admin(sender, instance, created, **kwargs):
    if not created:
        return
    with transaction.atomic():
        group = Group.objects.select_for_update().get(name=ADMIN_GROUP_NAME)
        if not group.user_set.exists():
            instance.is_staff = True
            instance.is_superuser = True
            instance.save(update_fields=("is_staff", "is_superuser"))
            group.user_set.add(instance)


@receiver(m2m_changed, sender=get_user_model().groups.through)
def make_admin_group_members_staff(sender, instance, action, pk_set, **kwargs):
    if action != "post_add":
        return
    User = get_user_model()
    if isinstance(instance, Group):
        if instance.name != ADMIN_GROUP_NAME:
            return
        User.objects.filter(pk__in=pk_set, is_staff=False).update(is_staff=True)
    elif (
        not instance.is_staff
        and Group.objects.filter(pk__in=pk_set, name=ADMIN_GROUP_NAME).exists()
    ):
        instance.is_staff = True
        instance.save(update_fields=("is_staff",))
