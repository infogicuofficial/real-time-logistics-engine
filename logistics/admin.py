from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import (AttendanceLog, Customer, FoodItem, FoodVariation, Order, OrderItem,
                     OrderItemEvent, RestaurantTable, Transaction, User)

@admin.register(User)
class AppUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (("Restaurant role", {"fields": ("role", "phone", "employee_code")}),)
    list_display = ("username", "first_name", "last_name", "role", "is_active")
    list_filter = ("role", "is_active")

class VariationInline(admin.TabularInline): model = FoodVariation; extra = 1
@admin.register(FoodItem)
class FoodItemAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "base_price", "primary_chef", "alternate_chef", "is_available")
    list_filter = ("kind", "is_available"); search_fields = ("name",); inlines = [VariationInline]

class OrderItemInline(admin.TabularInline): model = OrderItem; extra = 0; readonly_fields = ("placed_at",)
@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("id", "table", "waiter", "customer", "status", "opened_at")
    list_filter = ("status",); inlines = [OrderItemInline]

admin.site.register([RestaurantTable, Customer, AttendanceLog, Transaction, OrderItemEvent])
