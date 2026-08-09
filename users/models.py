from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Single user table for every department (§2 Roles & Access).

    Roles are boolean flags rather than Django Groups because the design doc
    treats them as fixed departments that drive the post-login landing page,
    and a user can legitimately hold two (e.g. Stores + Accounts).
    """

    is_salesman = models.BooleanField(default=False)
    is_sales_manager = models.BooleanField(default=False)
    is_designer = models.BooleanField(default=False)
    is_production_manager = models.BooleanField(default=False)
    is_floor_manager = models.BooleanField(default=False)
    is_stores = models.BooleanField(default=False)
    is_accounts = models.BooleanField(default=False)
    is_qa = models.BooleanField(default=False)

    # Shown on the login mockup as "Employee ID / Email"
    employee_id = models.CharField(max_length=30, blank=True, unique=True, null=True)
    phone = models.CharField(max_length=15, blank=True)

    # Order matters: the first flag a user holds decides their landing page.
    ROLE_FLAGS = [
        ('is_salesman', 'Sales'),
        ('is_sales_manager', 'Sales Manager'),
        ('is_accounts', 'Accounts'),
        ('is_designer', 'Design'),
        ('is_production_manager', 'Production Manager'),
        ('is_floor_manager', 'Production Floor Manager'),
        ('is_stores', 'Stores'),
        ('is_qa', 'QA'),
    ]

    @property
    def roles(self):
        return [label for flag, label in self.ROLE_FLAGS if getattr(self, flag)]

    @property
    def primary_role(self):
        return next(iter(self.roles), None)

    def __str__(self):
        return f'{self.get_full_name() or self.username} ({self.primary_role or "no role"})'
