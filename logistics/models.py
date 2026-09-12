from datetime import time, timedelta
from decimal import Decimal

from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


def validate_300_words(value):
    if len(value.split()) > 300:
        raise ValidationError("Description cannot exceed 300 words.")


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "ADMIN", "Admin"
        MANAGER = "MANAGER", "Manager"
        WAITER = "WAITER", "Waiter"
        CHEF = "CHEF", "Chef"

    role = models.CharField(max_length=10, choices=Role.choices, default=Role.WAITER, db_index=True)
    phone = models.CharField(max_length=24, blank=True)
    employee_code = models.CharField(max_length=24, unique=True, null=True, blank=True)

    @property
    def is_admin(self): return self.role == self.Role.ADMIN or self.is_superuser
    @property
    def is_manager(self): return self.role == self.Role.MANAGER
    @property
    def is_waiter(self): return self.role == self.Role.WAITER
    @property
    def is_chef(self): return self.role == self.Role.CHEF


class RestaurantTable(models.Model):
    class Status(models.TextChoices):
        AVAILABLE = "AVAILABLE", "Available"
        OCCUPIED = "OCCUPIED", "Occupied"
        RESERVED = "RESERVED", "Reserved"
        CLEANING = "CLEANING", "Cleaning"

    number = models.PositiveSmallIntegerField(unique=True)
    label = models.CharField(max_length=50, blank=True)
    capacity = models.PositiveSmallIntegerField(default=2, validators=[MinValueValidator(1)])
    zone = models.CharField(max_length=50, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.AVAILABLE, db_index=True)
    is_active = models.BooleanField(default=True)

    class Meta: ordering = ["number"]
    def __str__(self): return self.label or f"Table {self.number}"


# Backwards-friendly name while retaining a non-conflicting Python model name.
Table = RestaurantTable


class FoodItem(models.Model):
    class Kind(models.TextChoices):
        REGULAR = "REGULAR", "Regular food"
        LIQUID = "LIQUID", "Liquid"

    name = models.CharField(max_length=120, unique=True)
    description = models.TextField(validators=[validate_300_words])
    image = models.ImageField(upload_to="food/%Y/%m/", blank=True)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.REGULAR)
    base_price = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.00"))])
    profit_margin = models.DecimalField(max_digits=5, decimal_places=2, default=0,
        validators=[MinValueValidator(Decimal("0.00"))], help_text="Internal percentage; never exposed to waiter/customer views.")
    primary_chef = models.ForeignKey(User, on_delete=models.PROTECT, related_name="primary_foods",
        limit_choices_to={"role": User.Role.CHEF})
    alternate_chef = models.ForeignKey(User, on_delete=models.PROTECT, related_name="alternate_foods",
        limit_choices_to={"role": User.Role.CHEF})
    is_available = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta: ordering = ["name"]
    def clean(self):
        if self.primary_chef_id and self.primary_chef_id == self.alternate_chef_id:
            raise ValidationError({"alternate_chef": "Alternate chef must differ from primary chef."})
    def __str__(self): return self.name


class FoodVariation(models.Model):
    food_item = models.ForeignKey(FoodItem, on_delete=models.CASCADE, related_name="variations")
    name = models.CharField(max_length=60)
    quantity = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    unit = models.CharField(max_length=12, blank=True, help_text="e.g. ml, g, piece")
    price = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.00"))])
    is_available = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["food_item", "name"], name="unique_food_variation")]
        ordering = ["price"]
    def __str__(self): return f"{self.food_item} · {self.name}"


class Customer(models.Model):
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=24, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    def __str__(self): return self.name


class Order(models.Model):
    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        CHECKOUT = "CHECKOUT", "Awaiting payment"
        PAID = "PAID", "Paid"
        CANCELLED = "CANCELLED", "Cancelled"

    table = models.ForeignKey(RestaurantTable, on_delete=models.PROTECT, related_name="orders")
    waiter = models.ForeignKey(User, on_delete=models.PROTECT, related_name="orders_taken", limit_choices_to={"role": User.Role.WAITER})
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="orders")
    member_count = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1)])
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.OPEN, db_index=True)
    notes = models.CharField(max_length=300, blank=True)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("5.00"))
    opened_at = models.DateTimeField(auto_now_add=True, db_index=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta: ordering = ["-opened_at"]
    @property
    def subtotal(self): return sum((i.line_total for i in self.items.exclude(status=OrderItem.Status.CANCELLED)), Decimal("0"))
    @property
    def tax_amount(self): return (self.subtotal * self.tax_rate / Decimal("100")).quantize(Decimal("0.01"))
    @property
    def total(self): return self.subtotal + self.tax_amount
    def __str__(self): return f"Order #{self.pk} · {self.table}"


class OrderItem(models.Model):
    class Status(models.TextChoices):
        PLACED = "PLACED", "Placed"
        ACCEPTED = "ACCEPTED", "Accepted"
        PREPARING = "PREPARING", "Preparing"
        READY = "READY", "Ready to collect"
        SERVED = "SERVED", "Served"
        CANCELLED = "CANCELLED", "Cancelled"

    ACTIVE_QUEUE_STATUSES = (Status.PLACED, Status.ACCEPTED, Status.PREPARING)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    variation = models.ForeignKey(FoodVariation, on_delete=models.PROTECT, related_name="order_items")
    quantity = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1)])
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    assigned_chef = models.ForeignKey(User, on_delete=models.PROTECT, related_name="kitchen_items", limit_choices_to={"role": User.Role.CHEF})
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PLACED, db_index=True)
    special_request = models.CharField(max_length=240, blank=True)
    placed_at = models.DateTimeField(auto_now_add=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    ready_at = models.DateTimeField(null=True, blank=True)
    served_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["placed_at"]
        indexes = [models.Index(fields=["assigned_chef", "status"], name="chef_queue_idx")]
    @property
    def line_total(self): return self.unit_price * self.quantity
    @property
    def can_cancel(self): return self.status in {self.Status.PLACED, self.Status.ACCEPTED}
    def __str__(self): return f"{self.quantity}× {self.variation}"


class OrderItemEvent(models.Model):
    order_item = models.ForeignKey(OrderItem, on_delete=models.CASCADE, related_name="events")
    status = models.CharField(max_length=12, choices=OrderItem.Status.choices)
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="order_events")
    created_at = models.DateTimeField(auto_now_add=True)
    note = models.CharField(max_length=240, blank=True)
    class Meta: ordering = ["created_at"]


class AttendanceLog(models.Model):
    employee = models.ForeignKey(User, on_delete=models.CASCADE, related_name="attendance_logs")
    work_date = models.DateField(default=timezone.localdate)
    expected_start = models.TimeField(default=time(9, 0))
    grace_minutes = models.PositiveSmallIntegerField(default=30)
    expected_hours = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal("8.00"))
    checked_in_at = models.DateTimeField()
    checked_out_at = models.DateTimeField(null=True, blank=True)
    late_minutes = models.PositiveIntegerField(default=0, editable=False)
    penalty_minutes = models.PositiveIntegerField(default=0, editable=False)
    required_minutes = models.PositiveIntegerField(default=480, editable=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["employee", "work_date"], name="one_attendance_per_day")]
        ordering = ["-work_date"]

    def calculate_requirements(self):
        local_checkin = timezone.localtime(self.checked_in_at)
        start = local_checkin.replace(hour=self.expected_start.hour, minute=self.expected_start.minute, second=0, microsecond=0)
        penalty_starts = start + timedelta(minutes=self.grace_minutes)
        self.late_minutes = max(0, int((local_checkin - penalty_starts).total_seconds() // 60))
        self.penalty_minutes = self.late_minutes * 2
        self.required_minutes = round(float(self.expected_hours) * 60) + self.penalty_minutes

    def save(self, *args, **kwargs):
        self.calculate_requirements()
        super().save(*args, **kwargs)

    @property
    def worked_minutes(self):
        end = self.checked_out_at or timezone.now()
        return max(0, int((end - self.checked_in_at).total_seconds() // 60))
    @property
    def can_checkout(self): return self.worked_minutes >= self.required_minutes
    @property
    def progress_percent(self): return min(100, round(self.worked_minutes / max(self.required_minutes, 1) * 100))


class Transaction(models.Model):
    class Method(models.TextChoices):
        CASH = "CASH", "Cash"
        CARD = "CARD", "Card"
        BANK = "BANK", "Bank transfer"
        MOBILE = "MOBILE", "Mobile banking"
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        COMPLETED = "COMPLETED", "Completed"
        FAILED = "FAILED", "Failed"
        REFUNDED = "REFUNDED", "Refunded"

    order = models.OneToOneField(Order, on_delete=models.PROTECT, related_name="transaction")
    method = models.CharField(max_length=10, choices=Method.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2)
    total = models.DecimalField(max_digits=12, decimal_places=2)
    amount_received = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    change_given = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    transaction_id = models.CharField(max_length=120, blank=True, db_index=True)
    provider = models.CharField(max_length=80, blank=True)
    card_last_four = models.CharField(max_length=4, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        if self.method == self.Method.CASH and self.amount_received is not None and self.amount_received < self.total:
            raise ValidationError({"amount_received": "Received cash cannot be less than the total."})
        if self.method != self.Method.CASH and not self.transaction_id:
            raise ValidationError({"transaction_id": "A transaction reference is required for non-cash payments."})
