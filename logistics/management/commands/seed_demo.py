from django.core.management.base import BaseCommand
from django.db import transaction
from logistics.models import Customer, FoodItem, FoodVariation, Order, RestaurantTable, User
from logistics.services.routing import route_order_item

class Command(BaseCommand):
    help = 'Create an idempotent demonstration restaurant.'
    @transaction.atomic
    def handle(self, *args, **kwargs):
        accounts = [('admin','ADMIN','Avery','Stone'),('manager','MANAGER','Sofia','Marin'),('waiter','WAITER','Elena','Rossi'),('chef','CHEF','Marco','Bianchi'),('chef2','CHEF','Amélie','Laurent'),('chef3','CHEF','Theo','Jansen')]
        users = {}
        for username, role, first, last in accounts:
            user, _ = User.objects.get_or_create(username=username, defaults={'role':role,'first_name':first,'last_name':last,'email':f'{username}@example.com'})
            user.role=role; user.first_name=first; user.last_name=last; user.set_password('demo'); user.is_staff=role in ('ADMIN','MANAGER'); user.is_superuser=role=='ADMIN'; user.save(); users[username]=user
        for n in range(1,13):
            RestaurantTable.objects.get_or_create(number=n, defaults={'capacity': 2 if n<5 else 4 if n<10 else 6, 'zone':'Garden' if n>8 else 'Main room'})
        foods=[('Charred Sea Bass','Citrus beurre blanc, spring vegetables and dill.',24,'chef','chef2',['Regular']),('Wild Mushroom Risotto','Carnaroli rice, woodland mushrooms and aged parmesan.',19,'chef2','chef3',['Regular','Large']),('Garden Burrata','Heritage tomatoes, basil oil and toasted sourdough.',14,'chef3','chef',['To share']),('Sparkling Citrus','Fresh citrus, rosemary and chilled sparkling water.',7,'chef','chef2',['Regular 250ml','Classic 300ml','Large 500ml','Extra Large 750ml'])]
        for name, desc, price, primary, alternate, sizes in foods:
            food, _=FoodItem.objects.get_or_create(name=name, defaults={'description':desc,'base_price':price,'profit_margin':28,'primary_chef':users[primary],'alternate_chef':users[alternate], 'kind':'LIQUID' if 'Citrus' in name else 'REGULAR'})
            for idx,size in enumerate(sizes): FoodVariation.objects.get_or_create(food_item=food,name=size,defaults={'price':price+idx*2})
        if not Order.objects.filter(status=Order.Status.OPEN).exists():
            guest=Customer.objects.create(name='Maya Chen',phone='+1 555 0134'); table=RestaurantTable.objects.get(number=4); table.status=RestaurantTable.Status.OCCUPIED; table.save()
            order=Order.objects.create(table=table,waiter=users['waiter'],customer=guest,member_count=3)
            for variation in FoodVariation.objects.filter(food_item__name__in=['Charred Sea Bass','Wild Mushroom Risotto'])[:2]: route_order_item(order=order,variation=variation)
        self.stdout.write(self.style.SUCCESS('Demo ready. Accounts: admin, manager, waiter, chef — password: demo'))
