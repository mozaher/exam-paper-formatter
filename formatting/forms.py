from pathlib import Path

from django import forms

from .models import Paper, PaperTemplate, Section

SAMPLE_EXTENSIONS = (".tex", ".docx", ".pdf")
SAMPLE_MAX_BYTES = 5 * 1024 * 1024


class TemplateSampleUploadForm(forms.Form):
    name = forms.CharField(
        max_length=120,
        required=False,
        label="Template name",
        help_text="Optional — defaults to the file name.",
    )
    file = forms.FileField(
        label="Sample document",
        help_text="A .docx, .tex or .pdf file of dummy questions showing your "
        "desired formatting. It is analysed and immediately discarded — "
        "never stored.",
    )

    def clean_file(self):
        f = self.cleaned_data["file"]
        suffix = Path(f.name).suffix.lower()
        if suffix not in SAMPLE_EXTENSIONS:
            raise forms.ValidationError("Upload a .docx, .tex or .pdf file.")
        if f.size > SAMPLE_MAX_BYTES:
            raise forms.ValidationError("File too large (5 MB limit).")
        return f


class PaperTemplateForm(forms.ModelForm):
    candidate_fields_csv = forms.CharField(
        required=False,
        label="Cover fill-in fields",
        help_text="Comma-separated, e.g.: Name, Date, Class. Printed as "
        "“Name: ____” lines on the cover.",
    )

    class Meta:
        model = PaperTemplate
        fields = [
            "name",
            "institution_name",
            "subtitle",
            "cover_heading",
            "address_text",
            "footer_text",
            "default_instructions",
            "font",
            "paper_size",
        ]
        widgets = {
            "default_instructions": forms.Textarea(attrs={"rows": 4}),
            "address_text": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["candidate_fields_csv"].initial = ", ".join(
                self.instance.candidate_fields or []
            )

    def save(self, commit=True):
        template = super().save(commit=False)
        raw = self.cleaned_data.get("candidate_fields_csv", "")
        template.candidate_fields = [
            part.strip()[:40] for part in raw.split(",") if part.strip()
        ][:8]
        if commit:
            template.save()
        return template


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
