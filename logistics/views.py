import json
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, DecimalField, F, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .forms import CheckoutForm, FoodItemForm, StartServiceForm
from .models import (AttendanceLog, Customer, FoodItem, FoodVariation, Order, OrderItem,
                     OrderItemEvent, RestaurantTable, Transaction, User)
from .services.routing import route_order_item, transfer_item


def _role_required(*roles):
    def decorator(view):
        @login_required
        def wrapped(request, *args, **kwargs):
            if not (request.user.is_superuser or request.user.role in roles):
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return wrapped
    return decorator


def _json_error(message, status=400): return JsonResponse({"ok": False, "error": str(message)}, status=status)


@login_required
def dashboard(request):
    today = timezone.localdate()
    open_orders = Order.objects.filter(status__in=[Order.Status.OPEN, Order.Status.CHECKOUT])
    context = {
        "tables": RestaurantTable.objects.filter(is_active=True).annotate(active_orders=Count("orders", filter=Q(orders__status=Order.Status.OPEN))),
        "open_order_count": open_orders.count(),
        "staff_on_shift": AttendanceLog.objects.filter(work_date=today, checked_out_at__isnull=True).count(),
        "ready_count": OrderItem.objects.filter(status=OrderItem.Status.READY).count(),
    }
    if request.user.is_waiter:
        mine = Order.objects.filter(waiter=request.user, opened_at__date=today)
        context.update(my_orders=mine.select_related("table", "customer")[:6], my_order_count=mine.count(),
                       delivered_count=OrderItem.objects.filter(order__waiter=request.user, served_at__date=today).count(),
                       billed=Transaction.objects.filter(order__waiter=request.user, paid_at__date=today).aggregate(v=Sum("total"))["v"] or 0)
    elif request.user.is_chef:
        queue = OrderItem.objects.filter(assigned_chef=request.user, status__in=OrderItem.ACTIVE_QUEUE_STATUSES)
        context.update(queue_count=queue.count(), preparing_count=queue.filter(status=OrderItem.Status.PREPARING).count(),
                       chef_tables=RestaurantTable.objects.filter(orders__items__in=queue).distinct())
    else:
        revenue = []
        for offset in range(6, -1, -1):
            day = today - timedelta(days=offset)
            value = Transaction.objects.filter(paid_at__date=day, status=Transaction.Status.COMPLETED).aggregate(v=Sum("total"))["v"] or 0
            revenue.append({"day": day.strftime("%a"), "value": float(value)})
        context["revenue_data"] = json.dumps(revenue)
        context["today_revenue"] = Transaction.objects.filter(paid_at__date=today, status=Transaction.Status.COMPLETED).aggregate(v=Sum("total"))["v"] or 0
    return render(request, "logistics/home.html", context)


@_role_required(User.Role.WAITER, User.Role.MANAGER, User.Role.ADMIN)
def start_service(request):
    tables = RestaurantTable.objects.filter(is_active=True).order_by("number")
    selected_id = request.GET.get("table")
    if request.method == "POST":
        form = StartServiceForm(request.POST)
        table = get_object_or_404(tables, pk=request.POST.get("table_id"))
        if form.is_valid():
            if table.status == RestaurantTable.Status.OCCUPIED:
                messages.error(request, "That table is already being served.")
            else:
                customer = Customer.objects.create(name=form.cleaned_data["customer_name"], phone=form.cleaned_data["phone"])
                waiter = request.user
                if not waiter.is_waiter:
                    waiter = User.objects.filter(role=User.Role.WAITER, is_active=True).first()
                    if not waiter:
                        messages.error(request, "Create an active waiter before starting service.")
                        return redirect("start_service")
                order = Order.objects.create(table=table, waiter=waiter, customer=customer,
                    member_count=form.cleaned_data["members"], notes=form.cleaned_data["notes"])
                table.status = RestaurantTable.Status.OCCUPIED; table.save(update_fields=["status"])
                return redirect("service_order", order_id=order.pk)
    else: form = StartServiceForm()
    return render(request, "logistics/start_service.html", {"tables": tables, "form": form, "selected_id": selected_id})


@_role_required(User.Role.WAITER, User.Role.MANAGER, User.Role.ADMIN)
def service_order(request, order_id):
    order = get_object_or_404(Order.objects.select_related("table", "customer", "waiter").prefetch_related("items__variation__food_item"), pk=order_id)
    foods = FoodItem.objects.filter(is_available=True).prefetch_related("variations")
    return render(request, "logistics/service.html", {"order": order, "foods": foods})


@require_GET
@login_required
def order_state(request, order_id):
    order = get_object_or_404(Order, pk=order_id)
    if request.user.is_waiter and order.waiter_id != request.user.id: raise PermissionDenied
    items = [{"id": i.id, "name": i.variation.food_item.name, "variation": i.variation.name,
              "quantity": i.quantity, "status": i.status, "status_label": i.get_status_display(),
              "chef": i.assigned_chef.get_full_name() or i.assigned_chef.username,
              "line_total": str(i.line_total), "can_cancel": i.can_cancel}
             for i in order.items.select_related("variation__food_item", "assigned_chef")]
    return JsonResponse({"items": items, "subtotal": str(order.subtotal), "tax": str(order.tax_amount), "total": str(order.total)})


@require_POST
@_role_required(User.Role.WAITER, User.Role.MANAGER, User.Role.ADMIN)
def add_order_item(request, order_id):
    order = get_object_or_404(Order, pk=order_id, status=Order.Status.OPEN)
    try:
        payload = json.loads(request.body or "{}")
        variation = FoodVariation.objects.select_related("food_item").get(pk=payload.get("variation_id"), is_available=True, food_item__is_available=True)
        item = route_order_item(order=order, variation=variation, quantity=max(1, int(payload.get("quantity", 1))),
                                special_request=str(payload.get("special_request", ""))[:240])
        return JsonResponse({"ok": True, "item_id": item.pk, "chef": item.assigned_chef.get_full_name() or item.assigned_chef.username})
    except (ValueError, FoodVariation.DoesNotExist, ValidationError) as exc:
        message = exc.message if hasattr(exc, "message") else str(exc)
        return _json_error(message)


@require_POST
@login_required
def update_item_status(request, item_id):
    item = get_object_or_404(OrderItem.objects.select_related("order"), pk=item_id)
    try: target = json.loads(request.body or "{}").get("status")
    except ValueError: return _json_error("Invalid request.")
    transitions = {
        OrderItem.Status.PLACED: [OrderItem.Status.ACCEPTED, OrderItem.Status.CANCELLED],
        OrderItem.Status.ACCEPTED: [OrderItem.Status.PREPARING, OrderItem.Status.CANCELLED],
        OrderItem.Status.PREPARING: [OrderItem.Status.READY],
        OrderItem.Status.READY: [OrderItem.Status.SERVED],
    }
    if target not in transitions.get(item.status, []): return _json_error("That status transition is not allowed.")
    kitchen_steps = {OrderItem.Status.ACCEPTED, OrderItem.Status.PREPARING, OrderItem.Status.READY}
    if target in kitchen_steps and not ((request.user.is_chef and item.assigned_chef_id == request.user.id) or request.user.is_manager or request.user.is_admin): raise PermissionDenied
    if target == OrderItem.Status.SERVED and not (request.user.is_waiter or request.user.is_manager or request.user.is_admin): raise PermissionDenied
    item.status = target
    now = timezone.now()
    if target == OrderItem.Status.ACCEPTED: item.accepted_at = now
    if target == OrderItem.Status.READY: item.ready_at = now
    if target == OrderItem.Status.SERVED: item.served_at = now
    item.save()
    OrderItemEvent.objects.create(order_item=item, status=target, actor=request.user)
    return JsonResponse({"ok": True, "status": target, "label": item.get_status_display()})


@require_POST
@_role_required(User.Role.WAITER, User.Role.MANAGER, User.Role.ADMIN)
def cancel_item(request, item_id):
    item = get_object_or_404(OrderItem, pk=item_id)
    if not item.can_cancel: return _json_error("Preparation has started; ask a manager for assistance.", 409)
    item.status = OrderItem.Status.CANCELLED; item.save(update_fields=["status"])
    OrderItemEvent.objects.create(order_item=item, status=item.status, actor=request.user, note="Cancelled before preparation")
    return JsonResponse({"ok": True})


@require_POST
@_role_required(User.Role.CHEF, User.Role.MANAGER, User.Role.ADMIN)
def transfer_item_view(request, item_id):
    item = get_object_or_404(OrderItem, pk=item_id, status__in=OrderItem.ACTIVE_QUEUE_STATUSES)
    if request.user.is_chef and item.assigned_chef_id != request.user.id: raise PermissionDenied
    try:
        payload = json.loads(request.body or "{}")
        target = User.objects.get(pk=payload.get("chef_id"), role=User.Role.CHEF)
        transfer_item(item, target, request.user)
        return JsonResponse({"ok": True, "chef": target.get_full_name() or target.username})
    except (ValueError, User.DoesNotExist, ValidationError) as exc:
        return _json_error(str(exc))


@_role_required(User.Role.CHEF, User.Role.MANAGER, User.Role.ADMIN)
def kitchen(request):
    items = OrderItem.objects.filter(status__in=OrderItem.ACTIVE_QUEUE_STATUSES).select_related("order__table", "variation__food_item", "assigned_chef")
    if request.user.is_chef: items = items.filter(assigned_chef=request.user)
    chefs = User.objects.filter(role=User.Role.CHEF, is_active=True).annotate(queue=Count("kitchen_items", filter=Q(kitchen_items__status__in=OrderItem.ACTIVE_QUEUE_STATUSES)))
    return render(request, "logistics/kitchen.html", {
        "placed_items": items.filter(status=OrderItem.Status.PLACED),
        "accepted_items": items.filter(status=OrderItem.Status.ACCEPTED),
        "preparing_items": items.filter(status=OrderItem.Status.PREPARING),
        "chefs": chefs,
    })


@require_POST
@_role_required(User.Role.WAITER, User.Role.MANAGER, User.Role.ADMIN)
@transaction.atomic
def checkout(request, order_id):
    order = get_object_or_404(Order.objects.select_for_update(), pk=order_id, status__in=[Order.Status.OPEN, Order.Status.CHECKOUT])
    form = CheckoutForm(request.POST)
    if not form.is_valid(): return _json_error(form.errors.get_json_data())
    data = form.cleaned_data; method = data["method"]
    if order.items.filter(status__in=OrderItem.ACTIVE_QUEUE_STATUSES).exists(): return _json_error("Kitchen items are still in progress.", 409)
    if not order.items.filter(status=OrderItem.Status.SERVED).exists(): return _json_error("Serve at least one item before checkout.", 409)
    if method == Transaction.Method.CASH:
        received = data.get("amount_received")
        if received is None or received < order.total: return _json_error("Received cash must cover the total.")
        change = received - order.total
    else:
        if not data.get("transaction_id"): return _json_error("A transaction reference is required.")
        received, change = None, Decimal("0")
    Transaction.objects.create(order=order, method=method, status=Transaction.Status.COMPLETED,
        subtotal=order.subtotal, tax_amount=order.tax_amount, total=order.total, amount_received=received,
        change_given=change, transaction_id=data.get("transaction_id", ""), provider=data.get("provider", ""),
        card_last_four=data.get("card_last_four", ""), paid_at=timezone.now())
    order.status = Order.Status.PAID; order.closed_at = timezone.now(); order.save(update_fields=["status", "closed_at"])
    order.table.status = RestaurantTable.Status.CLEANING; order.table.save(update_fields=["status"])
    return JsonResponse({"ok": True, "receipt_url": f"/receipt/{order.pk}/"})


@login_required
def receipt(request, order_id):
    order = get_object_or_404(Order.objects.select_related("table", "customer", "waiter", "transaction").prefetch_related("items__variation__food_item"), pk=order_id, status=Order.Status.PAID)
    return render(request, "logistics/receipt.html", {"order": order})


@login_required
def attendance(request):
    today = timezone.localdate(); log = AttendanceLog.objects.filter(employee=request.user, work_date=today).first()
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "checkin" and not log:
            log = AttendanceLog.objects.create(employee=request.user, checked_in_at=timezone.now())
            messages.success(request, "Check-in recorded. Your shift timer is running.")
        elif action == "checkout" and log and not log.checked_out_at:
            if not log.can_checkout: messages.error(request, "Required work time has not yet been completed.")
            else:
                log.checked_out_at = timezone.now(); log.save(update_fields=["checked_out_at"])
                messages.success(request, "Day completed. Thank you.")
        return redirect("attendance")
    history = AttendanceLog.objects.filter(employee=request.user)[:14]
    return render(request, "logistics/attendance.html", {"log": log, "history": history})


@_role_required(User.Role.MANAGER, User.Role.ADMIN)
def add_food(request):
    if request.method == "POST":
        form = FoodItemForm(request.POST, request.FILES)
        if form.is_valid():
            with transaction.atomic():
                food = form.save()
                for row in form.cleaned_data["variations_json"]:
                    FoodVariation.objects.create(food_item=food, name=row["name"][:60], quantity=row.get("quantity") or None,
                        unit=str(row.get("unit", ""))[:12], price=Decimal(str(row["price"])), is_available=True)
            messages.success(request, f"{food.name} is now available to the service team.")
            return redirect("add_food")
    else: form = FoodItemForm()
    return render(request, "logistics/add_food.html", {"form": form})
