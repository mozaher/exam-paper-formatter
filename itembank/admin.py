from django.contrib import admin

from .models import Choice, Item, Topic


class ChoiceInline(admin.TabularInline):
    model = Choice
    extra = 0


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ["title", "org", "item_type", "topic", "difficulty", "marks", "status"]
    list_filter = ["org", "item_type", "difficulty", "cognitive_level", "status"]
    search_fields = ["title", "body"]
    inlines = [ChoiceInline]


@admin.register(Topic)
class TopicAdmin(admin.ModelAdmin):
    list_display = ["name", "parent", "org"]
    list_filter = ["org"]
