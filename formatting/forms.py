from django import forms

from .models import Paper, PaperTemplate, Section


class PaperTemplateForm(forms.ModelForm):
    class Meta:
        model = PaperTemplate
        fields = [
            "name",
            "institution_name",
            "subtitle",
            "footer_text",
            "default_instructions",
            "font",
            "paper_size",
        ]
        widgets = {"default_instructions": forms.Textarea(attrs={"rows": 4})}


class PaperForm(forms.ModelForm):
    class Meta:
        model = Paper
        fields = [
            "title",
            "template",
            "course_code",
            "exam_date",
            "duration_minutes",
            "instructions",
            "include_answer_space",
        ]
        widgets = {
            "instructions": forms.Textarea(attrs={"rows": 4}),
            "exam_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, org=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["template"].required = False
        self.fields["template"].queryset = (
            PaperTemplate.objects.for_org(org) if org is not None else PaperTemplate.objects.none()
        )


class SectionForm(forms.ModelForm):
    class Meta:
        model = Section
        fields = ["title", "instructions"]
        widgets = {"instructions": forms.Textarea(attrs={"rows": 2})}
