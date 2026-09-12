from datetime import datetime, time, timezone as dt_timezone
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from .models import AttendanceLog, Customer, FoodItem, FoodVariation, Order, OrderItem, RestaurantTable, User
from .services.routing import route_order_item

class RoutingTests(TestCase):
    def setUp(self):
        self.waiter = User.objects.create_user('waiter', role=User.Role.WAITER)
        self.chefs = [User.objects.create_user(f'chef{i}', role=User.Role.CHEF) for i in range(3)]
        self.table = RestaurantTable.objects.create(number=1)
        self.customer = Customer.objects.create(name='Guest')
        self.order = Order.objects.create(table=self.table, waiter=self.waiter, customer=self.customer)
        self.food = FoodItem.objects.create(name='Risotto', description='Rice', base_price=10,
            primary_chef=self.chefs[0], alternate_chef=self.chefs[1])
        self.variation = FoodVariation.objects.create(food_item=self.food, name='Regular', price=10)

    def _fill(self, chef, amount):
        for _ in range(amount):
            OrderItem.objects.create(order=self.order, variation=self.variation, unit_price=10, assigned_chef=chef)

    def test_primary_then_alternate_then_least_loaded(self):
        first = route_order_item(order=self.order, variation=self.variation)
        self.assertEqual(first.assigned_chef, self.chefs[0])
        self._fill(self.chefs[0], 9)  # primary now has ten
        alternate = route_order_item(order=self.order, variation=self.variation)
        self.assertEqual(alternate.assigned_chef, self.chefs[1])
        self._fill(self.chefs[1], 9)  # alternate now has ten
        fallback = route_order_item(order=self.order, variation=self.variation)
        self.assertEqual(fallback.assigned_chef, self.chefs[2])

    def test_rejects_when_entire_kitchen_is_full(self):
        for chef in self.chefs: self._fill(chef, 10)
        with self.assertRaises(ValidationError): route_order_item(order=self.order, variation=self.variation)

class AttendanceTests(TestCase):
    def setUp(self): self.user = User.objects.create_user('staff')
    def test_penalty_is_double_minutes_after_grace(self):
        # UTC test setting: 09:40 is ten minutes beyond a 09:30 grace boundary.
        checkin = datetime(2026, 9, 12, 9, 40, tzinfo=dt_timezone.utc)
        log = AttendanceLog.objects.create(employee=self.user, work_date=checkin.date(), checked_in_at=checkin)
        self.assertEqual(log.late_minutes, 10)
        self.assertEqual(log.penalty_minutes, 20)
        self.assertEqual(log.required_minutes, 500)
