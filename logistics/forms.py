import json
from django import forms
from django.core.exceptions import ValidationError
from .models import Customer, FoodItem, Order, Transaction


class StartServiceForm(forms.Form):
    customer_name = forms.CharField(max_length=120)
    phone = forms.CharField(max_length=24, required=False)
    members = forms.IntegerField(min_value=1, max_value=30, initial=2)
    notes = forms.CharField(max_length=300, required=False)


class FoodItemForm(forms.ModelForm):
    variations_json = forms.CharField(widget=forms.HiddenInput)
    class Meta:
        model = FoodItem
        fields = ["name", "description", "image", "kind", "base_price", "profit_margin", "primary_chef", "alternate_chef"]
    def clean_variations_json(self):
        try: rows = json.loads(self.cleaned_data["variations_json"])
        except (ValueError, TypeError): raise ValidationError("Variation data is invalid.")
        if not rows: raise ValidationError("Add at least one size or quantity.")
        for row in rows:
            if not row.get("name") or float(row.get("price", -1)) < 0: raise ValidationError("Every variation needs a name and valid price.")
        return rows


class CheckoutForm(forms.Form):
    method = forms.ChoiceField(choices=Transaction.Method.choices)
    amount_received = forms.DecimalField(max_digits=12, decimal_places=2, required=False)
    transaction_id = forms.CharField(max_length=120, required=False)
    provider = forms.CharField(max_length=80, required=False)
    card_last_four = forms.RegexField(regex=r"^\d{4}$", required=False)
