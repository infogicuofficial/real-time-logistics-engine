"""Concurrency-safe kitchen queue routing."""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q

from logistics.models import FoodVariation, Order, OrderItem, OrderItemEvent, User

QUEUE_CAPACITY = 10


def _queue_counts(chef_ids):
    return dict(
        OrderItem.objects.filter(
            assigned_chef_id__in=chef_ids,
            status__in=OrderItem.ACTIVE_QUEUE_STATUSES,
        ).values("assigned_chef_id").annotate(total=Count("id")).values_list("assigned_chef_id", "total")
    )


@transaction.atomic
def route_order_item(*, order: Order, variation: FoodVariation, quantity: int = 1, special_request: str = "") -> OrderItem:
    """Route primary → alternate → globally least-loaded chef.

    Chef rows are locked until creation, ensuring simultaneous orders cannot both
    claim the same last queue slot.
    """
    food = variation.food_item
    chefs = list(User.objects.select_for_update().filter(role=User.Role.CHEF, is_active=True).order_by("id"))
    if not chefs:
        raise ValidationError("No active chefs are available.")
    counts = _queue_counts([chef.id for chef in chefs])
    by_id = {chef.id: chef for chef in chefs}

    selected = None
    for preferred_id in (food.primary_chef_id, food.alternate_chef_id):
        if preferred_id in by_id and counts.get(preferred_id, 0) < QUEUE_CAPACITY:
            selected = by_id[preferred_id]
            break
    if selected is None:
        eligible = [chef for chef in chefs if counts.get(chef.id, 0) < QUEUE_CAPACITY]
        if not eligible:
            raise ValidationError("Kitchen is at capacity. Please pause ordering briefly.")
        selected = min(eligible, key=lambda chef: (counts.get(chef.id, 0), chef.id))

    item = OrderItem.objects.create(order=order, variation=variation, quantity=quantity,
        unit_price=variation.price, assigned_chef=selected, special_request=special_request)
    OrderItemEvent.objects.create(order_item=item, status=item.status, actor=order.waiter, note="Routed to kitchen")
    return item


@transaction.atomic
def transfer_item(item: OrderItem, target: User, actor: User):
    User.objects.select_for_update().filter(pk=target.pk).get()
    active = OrderItem.objects.filter(assigned_chef=target, status__in=OrderItem.ACTIVE_QUEUE_STATUSES).count()
    if target.role != User.Role.CHEF or not target.is_active or active >= QUEUE_CAPACITY:
        raise ValidationError("That chef cannot accept another item.")
    item.assigned_chef = target
    item.save(update_fields=["assigned_chef"])
    OrderItemEvent.objects.create(order_item=item, status=item.status, actor=actor, note=f"Transferred to {target.get_full_name() or target.username}")
    return item
