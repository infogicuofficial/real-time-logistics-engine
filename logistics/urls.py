from django.urls import path
from . import views
urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('service/start/', views.start_service, name='start_service'),
    path('service/<int:order_id>/', views.service_order, name='service_order'),
    path('service/<int:order_id>/state/', views.order_state, name='order_state'),
    path('service/<int:order_id>/items/', views.add_order_item, name='add_order_item'),
    path('items/<int:item_id>/status/', views.update_item_status, name='update_item_status'),
    path('items/<int:item_id>/cancel/', views.cancel_item, name='cancel_item'),
    path('items/<int:item_id>/transfer/', views.transfer_item_view, name='transfer_item'),
    path('kitchen/', views.kitchen, name='kitchen'),
    path('checkout/<int:order_id>/', views.checkout, name='checkout'),
    path('receipt/<int:order_id>/', views.receipt, name='receipt'),
    path('attendance/', views.attendance, name='attendance'),
    path('food/add/', views.add_food, name='add_food'),
]
