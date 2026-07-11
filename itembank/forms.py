from django import forms
from django.core.exceptions import ValidationError
from django.forms.models import BaseInlineFormSet, inlineformset_factory

from . import itemtypes
from .models import Choice, Item, Topic


class ItemForm(forms.ModelForm):
    class Meta:
        model = Item
        fields = [
            "title",
            "body",
            "topic",
            "difficulty",
            "cognitive_level",
            "marks",
            "status",
            "model_answer",
        ]
        widgets = {
            "body": forms.Textarea(attrs={"rows": 4}),
            "model_answer": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, org=None, item_type=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["topic"].required = False
        self.fields["topic"].queryset = (
            Topic.objects.for_org(org).select_related("parent")
            if org is not None
            else Topic.objects.none()
        )
        spec = itemtypes.get_type(item_type) if item_type else None
        if spec is not None and not spec.has_model_answer:
            self.fields.pop("model_answer")


class ChoiceForm(forms.ModelForm):
    class Meta:
        model = Choice
        fields = ["text", "is_correct"]
        widgets = {"text": forms.TextInput(attrs={"placeholder": "Answer choice"})}


class BaseChoiceFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        pairs = []
        for form in self.forms:
            if not form.cleaned_data or form.cleaned_data.get("DELETE"):
                continue
            text = form.cleaned_data.get("text", "")
            if text and text.strip():
                pairs.append((text, form.cleaned_data.get("is_correct", False)))
        itemtypes.validate_choices("mcq", pairs)


ChoiceFormSet = inlineformset_factory(
    Item,
    Choice,
    form=ChoiceForm,
    formset=BaseChoiceFormSet,
    fields=["text", "is_correct"],
    extra=5,
    can_delete=True,
)


class TopicForm(forms.ModelForm):
    class Meta:
        model = Topic
        fields = ["name", "parent"]

    def __init__(self, *args, org=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.org = org
        self.fields["parent"].required = False
        self.fields["parent"].queryset = (
            Topic.objects.for_org(org).filter(parent__isnull=True)
            if org is not None
            else Topic.objects.none()
        )

    def clean(self):
        cleaned = super().clean()
        name, parent = cleaned.get("name"), cleaned.get("parent")
        if name and self.org is not None:
            exists = Topic.objects.for_org(self.org).filter(
                parent=parent, name__iexact=name.strip()
            )
            if self.instance.pk:
                exists = exists.exclude(pk=self.instance.pk)
            if exists.exists():
                raise ValidationError("A topic with that name already exists there.")
        return cleaned


class QtiImportForm(forms.Form):
    file = forms.FileField(
        label="QTI 3.0 file",
        help_text="A QTI 3.0 content package (.zip) or a single assessment-item XML file.",
    )

    def clean_file(self):
        f = self.cleaned_data["file"]
        if f.size > 20 * 1024 * 1024:
            raise ValidationError("File too large (20 MB limit).")
        return f
