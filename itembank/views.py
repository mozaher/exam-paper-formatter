from django.contrib import messages
from django.db import transaction
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify
from django.views import View

from core import billing
from core.access import ModuleRequiredMixin

from .forms import ChoiceFormSet, ItemForm, QtiImportForm, TopicForm
from . import itemtypes
from .models import Item, Topic
from .qti import export as qti_export
from .qti import importer as qti_importer


class ItembankMixin(ModuleRequiredMixin):
    required_module = "itembank"


def _base_queryset(org):
    return Item.objects.for_org(org).select_related("topic", "topic__parent")


class ItemListView(ItembankMixin, View):
    def get(self, request):
        qs = _base_queryset(request.org)
        f = {
            "q": request.GET.get("q", "").strip(),
            "topic": request.GET.get("topic", ""),
            "item_type": request.GET.get("item_type", ""),
            "difficulty": request.GET.get("difficulty", ""),
            "cognitive_level": request.GET.get("cognitive_level", ""),
            "status": request.GET.get("status", ""),
        }
        if f["q"]:
            from django.db.models import Q

            qs = qs.filter(Q(title__icontains=f["q"]) | Q(body__icontains=f["q"]))
        if f["topic"].isdigit():
            qs = qs.filter(topic_id=int(f["topic"]))
        if f["item_type"]:
            qs = qs.filter(item_type=f["item_type"])
        if f["difficulty"]:
            qs = qs.filter(difficulty=f["difficulty"])
        if f["cognitive_level"]:
            qs = qs.filter(cognitive_level=f["cognitive_level"])
        if f["status"]:
            qs = qs.filter(status=f["status"])
        else:
            qs = qs.exclude(status=Item.Status.ARCHIVED)

        max_items = billing.get_limit(request.org, "max_items")
        total = Item.objects.for_org(request.org).count()
        return render(
            request,
            "itembank/item_list.html",
            {
                "items": qs[:500],
                "filters": f,
                "topics": Topic.objects.for_org(request.org).select_related("parent"),
                "item_types": itemtypes.all_types(),
                "difficulties": Item.Difficulty.choices,
                "cognitive_levels": Item.CognitiveLevel.choices,
                "statuses": Item.Status.choices,
                "at_limit": max_items is not None and total >= max_items,
                "max_items": max_items,
            },
        )


class ItemCreateView(ItembankMixin, View):
    def get(self, request):
        spec = itemtypes.get_type(request.GET.get("type", "mcq"))
        if spec is None:
            messages.error(request, "Unknown question type.")
            return redirect("itembank:item_list")
        form = ItemForm(org=request.org, item_type=spec.code)
        formset = ChoiceFormSet(prefix="choices") if spec.has_choices else None
        return render(
            request,
            "itembank/item_form.html",
            {"form": form, "formset": formset, "spec": spec, "is_new": True},
        )

    def post(self, request):
        spec = itemtypes.get_type(request.POST.get("item_type", ""))
        if spec is None:
            messages.error(request, "Unknown question type.")
            return redirect("itembank:item_list")

        max_items = billing.get_limit(request.org, "max_items")
        if max_items is not None and Item.objects.for_org(request.org).count() >= max_items:
            messages.error(
                request,
                f"Your plan allows up to {max_items} questions. "
                "Upgrade on the billing page to add more.",
            )
            return redirect("itembank:item_list")

        form = ItemForm(request.POST, org=request.org, item_type=spec.code)
        formset = ChoiceFormSet(request.POST, prefix="choices") if spec.has_choices else None
        if form.is_valid() and (formset is None or formset.is_valid()):
            with transaction.atomic():
                item = form.save(commit=False)
                item.org = request.org
                item.item_type = spec.code
                item.created_by = request.user
                item.save()
                if formset is not None:
                    formset.instance = item
                    self._save_choices(formset)
            messages.success(request, f"“{item.title}” saved.")
            return redirect("itembank:item_detail", pk=item.pk)
        return render(
            request,
            "itembank/item_form.html",
            {"form": form, "formset": formset, "spec": spec, "is_new": True},
        )

    @staticmethod
    def _save_choices(formset):
        formset.save()
        for i, choice in enumerate(formset.instance.choices.all()):
            if choice.order != i:
                choice.order = i
                choice.save(update_fields=["order"])


class ItemUpdateView(ItembankMixin, View):
    def get_item(self, request, pk):
        return get_object_or_404(_base_queryset(request.org), pk=pk)

    def get(self, request, pk):
        item = self.get_item(request, pk)
        spec = itemtypes.get_type(item.item_type)
        form = ItemForm(instance=item, org=request.org, item_type=item.item_type)
        formset = (
            ChoiceFormSet(instance=item, prefix="choices") if spec.has_choices else None
        )
        return render(
            request,
            "itembank/item_form.html",
            {"form": form, "formset": formset, "spec": spec, "item": item, "is_new": False},
        )

    def post(self, request, pk):
        item = self.get_item(request, pk)
        spec = itemtypes.get_type(item.item_type)
        form = ItemForm(request.POST, instance=item, org=request.org, item_type=item.item_type)
        formset = (
            ChoiceFormSet(request.POST, instance=item, prefix="choices")
            if spec.has_choices
            else None
        )
        if form.is_valid() and (formset is None or formset.is_valid()):
            with transaction.atomic():
                form.save()
                if formset is not None:
                    ItemCreateView._save_choices(formset)
            messages.success(request, f"“{item.title}” updated.")
            return redirect("itembank:item_detail", pk=item.pk)
        return render(
            request,
            "itembank/item_form.html",
            {"form": form, "formset": formset, "spec": spec, "item": item, "is_new": False},
        )


class ItemDetailView(ItembankMixin, View):
    def get(self, request, pk):
        item = get_object_or_404(_base_queryset(request.org), pk=pk)
        return render(request, "itembank/item_detail.html", {"item": item})


class ItemArchiveView(ItembankMixin, View):
    def post(self, request, pk):
        item = get_object_or_404(_base_queryset(request.org), pk=pk)
        item.status = Item.Status.ARCHIVED
        item.save(update_fields=["status"])
        messages.success(request, f"“{item.title}” archived.")
        return redirect("itembank:item_list")


class ItemQtiXmlView(ItembankMixin, View):
    def get(self, request, pk):
        item = get_object_or_404(_base_queryset(request.org), pk=pk)
        xml = qti_export.item_to_qti_xml(item)
        response = HttpResponse(xml, content_type="application/xml")
        response["Content-Disposition"] = (
            f'attachment; filename="{item.qti_identifier}.xml"'
        )
        return response


class TopicListView(ItembankMixin, View):
    def get(self, request):
        return self._render(request, TopicForm(org=request.org))

    def post(self, request):
        form = TopicForm(request.POST, org=request.org)
        if form.is_valid():
            topic = form.save(commit=False)
            topic.org = request.org
            topic.save()
            messages.success(request, f"Topic “{topic}” added.")
            return redirect("itembank:topic_list")
        return self._render(request, form)

    def _render(self, request, form):
        topics = (
            Topic.objects.for_org(request.org)
            .filter(parent__isnull=True)
            .prefetch_related("children")
        )
        return render(request, "itembank/topic_list.html", {"topics": topics, "form": form})


class TopicDeleteView(ItembankMixin, View):
    def post(self, request, pk):
        topic = get_object_or_404(Topic.objects.for_org(request.org), pk=pk)
        if topic.children.exists():
            messages.error(request, "Delete or move its subtopics first.")
        else:
            n = topic.items.count()
            topic.delete()
            note = f" ({n} question{'s' if n != 1 else ''} left untagged)" if n else ""
            messages.success(request, f"Topic deleted{note}.")
        return redirect("itembank:topic_list")


class QtiExportView(ItembankMixin, View):
    def get(self, request):
        count = (
            Item.objects.for_org(request.org).exclude(status=Item.Status.ARCHIVED).count()
        )
        return render(request, "itembank/qti_export.html", {"count": count})

    def post(self, request):
        items = (
            _base_queryset(request.org)
            .exclude(status=Item.Status.ARCHIVED)
            .prefetch_related("choices")
        )
        data = qti_export.build_package(items)
        import io

        filename = f"{slugify(request.org.name) or 'itembank'}-qti3.zip"
        return FileResponse(
            io.BytesIO(data), as_attachment=True, filename=filename,
            content_type="application/zip",
        )


class QtiImportView(ItembankMixin, View):
    def get(self, request):
        return render(request, "itembank/qti_import.html", {"form": QtiImportForm()})

    def post(self, request):
        form = QtiImportForm(request.POST, request.FILES)
        if not form.is_valid():
            return render(request, "itembank/qti_import.html", {"form": form})
        f = form.cleaned_data["file"]
        result = qti_importer.import_upload(request.org, request.user, f.name, f.read())
        return render(
            request,
            "itembank/qti_import_result.html",
            {"result": result},
        )
