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
    # Shop-floor operator who works a station. Not one of the 8 department
    # roles — it grants no screens, it only makes the user pickable as an
    # operator on a job card station.
    is_operator = models.BooleanField(default=False)

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

    @property
    def short_name(self):
        """'Arjun P.' — the form every screen displays."""
        if self.first_name and self.last_name:
            return f'{self.first_name} {self.last_name[0]}.'
        return self.first_name or self.username

    def __str__(self):
        return f'{self.get_full_name() or self.username} ({self.primary_role or "no role"})'
