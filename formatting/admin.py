from django.contrib import admin

from .models import Paper, PaperQuestion, PaperTemplate, Section


class SectionInline(admin.TabularInline):
    model = Section
    extra = 0


@admin.register(Paper)
class PaperAdmin(admin.ModelAdmin):
    list_display = ["title", "org", "course_code", "exam_date", "updated_at"]
    list_filter = ["org"]
    inlines = [SectionInline]


@admin.register(PaperTemplate)
class PaperTemplateAdmin(admin.ModelAdmin):
    list_display = ["name", "org", "institution_name", "font", "paper_size"]
    list_filter = ["org"]


admin.site.register(PaperQuestion)
