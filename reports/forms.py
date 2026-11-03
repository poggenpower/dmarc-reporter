from django import forms
from django.contrib.auth.models import Permission


class AccountEmailForm(forms.Form):
    email = forms.EmailField(required=False)


class ApiTokenCreateForm(forms.Form):
    name = forms.CharField(max_length=100)
    permissions = forms.ModelMultipleChoiceField(
        queryset=Permission.objects.none(),
        widget=forms.CheckboxSelectMultiple,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["permissions"].queryset = Permission.objects.filter(
            content_type__app_label="reports",
            codename__startswith="api_",
        ).order_by("codename")


class DashboardFilterForm(forms.Form):
    begin = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    end = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    domain = forms.ChoiceField(required=False)
    reporter = forms.ChoiceField(required=False)
    source_ip = forms.GenericIPAddressField(required=False)
    non_aligned = forms.BooleanField(required=False, initial=True)
    exclude_spf_pass = forms.BooleanField(required=False, initial=True)
    disposition = forms.ChoiceField(
        required=False,
        choices=(
            ("", "All dispositions"),
            ("none", "None"),
            ("quarantine", "Quarantine"),
            ("reject", "Reject"),
        ),
    )

    def __init__(self, *args, domains=(), reporters=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["domain"].choices = [
            ("", "All domains"),
            *((domain, domain) for domain in domains),
        ]
        self.fields["reporter"].choices = [
            ("", "All destinations"),
            *((reporter, reporter) for reporter in reporters),
        ]

    def clean(self):
        cleaned_data = super().clean()
        begin = cleaned_data.get("begin")
        end = cleaned_data.get("end")
        if begin and end and begin > end:
            raise forms.ValidationError("The start date must not be after the end date.")
        return cleaned_data
