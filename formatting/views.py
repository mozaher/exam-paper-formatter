from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from core.access import ModuleRequiredMixin
from itembank.models import Item, Topic

from . import pdf
from .forms import PaperForm, PaperTemplateForm, SectionForm
from .models import Paper, PaperQuestion, PaperTemplate, Section


class FormattingMixin(ModuleRequiredMixin):
    required_module = "formatting"


def _get_paper(request, pk):
    return get_object_or_404(
        Paper.objects.for_org(request.org).select_related("template"), pk=pk
    )


def _get_section(request, pk):
    return get_object_or_404(
        Section.objects.select_related("paper"), pk=pk, paper__org=request.org
    )


def _normalize_orders(queryset):
    """Rewrite order fields to 0..n-1 in current display order."""
    items = list(queryset)
    for i, obj in enumerate(items):
        if obj.order != i:
            obj.order = i
            obj.save(update_fields=["order"])
    return items


def _move(queryset, target, direction):
    items = _normalize_orders(queryset)
    idx = next((i for i, o in enumerate(items) if o.pk == target.pk), None)
    if idx is None:
        return
    swap = idx - 1 if direction == "up" else idx + 1
    if 0 <= swap < len(items):
        items[idx].order, items[swap].order = items[swap].order, items[idx].order
        items[idx].save(update_fields=["order"])
        items[swap].save(update_fields=["order"])


def _numbered_sections(paper):
    """Sections with continuous question numbering across the whole paper."""
    data = []
    number = 0
    for section in paper.sections.prefetch_related("questions__item__topic"):
        rows = []
        for pq in section.questions.select_related("item"):
            number += 1
            rows.append({"num": number, "pq": pq})
        data.append({"section": section, "rows": rows})
    return data


class PaperListView(FormattingMixin, View):
    def get(self, request):
        papers = Paper.objects.for_org(request.org).select_related("template")
        return render(request, "formatting/paper_list.html", {"papers": papers})


class PaperCreateView(FormattingMixin, View):
    def get(self, request):
        return render(
            request,
            "formatting/paper_form.html",
            {"form": PaperForm(org=request.org), "is_new": True},
        )

    def post(self, request):
        form = PaperForm(request.POST, org=request.org)
        if not form.is_valid():
            return render(
                request, "formatting/paper_form.html", {"form": form, "is_new": True}
            )
        paper = form.save(commit=False)
        paper.org = request.org
        paper.created_by = request.user
        paper.save()
        Section.objects.create(paper=paper, title="Section A", order=0)
        messages.success(request, f"“{paper.title}” created. Now add questions to it.")
        return redirect("formatting:paper_detail", pk=paper.pk)


class PaperUpdateView(FormattingMixin, View):
    def get(self, request, pk):
        paper = _get_paper(request, pk)
        return render(
            request,
            "formatting/paper_form.html",
            {"form": PaperForm(instance=paper, org=request.org), "paper": paper, "is_new": False},
        )

    def post(self, request, pk):
        paper = _get_paper(request, pk)
        form = PaperForm(request.POST, instance=paper, org=request.org)
        if not form.is_valid():
            return render(
                request,
                "formatting/paper_form.html",
                {"form": form, "paper": paper, "is_new": False},
            )
        form.save()
        messages.success(request, "Paper updated.")
        return redirect("formatting:paper_detail", pk=paper.pk)


class PaperDeleteView(FormattingMixin, View):
    def post(self, request, pk):
        paper = _get_paper(request, pk)
        title = paper.title
        paper.delete()
        messages.success(request, f"“{title}” deleted.")
        return redirect("formatting:paper_list")


class PaperDetailView(FormattingMixin, View):
    def get(self, request, pk):
        paper = _get_paper(request, pk)
        return render(
            request,
            "formatting/paper_detail.html",
            {
                "paper": paper,
                "sections_data": _numbered_sections(paper),
                "section_form": SectionForm(),
            },
        )


class SectionCreateView(FormattingMixin, View):
    def post(self, request, pk):
        paper = _get_paper(request, pk)
        form = SectionForm(request.POST)
        if form.is_valid():
            section = form.save(commit=False)
            section.paper = paper
            if not section.title.strip():
                section.title = f"Section {chr(65 + paper.sections.count())}"
            section.order = paper.sections.count()
            section.save()
            messages.success(request, f"“{section.title}” added.")
        else:
            messages.error(request, "Could not add section — give it a title.")
        return redirect("formatting:paper_detail", pk=paper.pk)


class SectionDeleteView(FormattingMixin, View):
    def post(self, request, pk):
        section = _get_section(request, pk)
        paper_pk = section.paper_id
        section.delete()
        messages.success(request, "Section removed.")
        return redirect("formatting:paper_detail", pk=paper_pk)


class SectionMoveView(FormattingMixin, View):
    def post(self, request, pk):
        section = _get_section(request, pk)
        _move(section.paper.sections.all(), section, request.POST.get("direction", ""))
        return redirect("formatting:paper_detail", pk=section.paper_id)


class QuestionPickerView(FormattingMixin, View):
    """Pick bank questions to add to one section."""

    def get(self, request, pk):
        section = _get_section(request, pk)
        paper = section.paper
        used_ids = set(
            PaperQuestion.objects.filter(section__paper=paper).values_list("item_id", flat=True)
        )
        qs = (
            Item.objects.for_org(request.org)
            .exclude(status=Item.Status.ARCHIVED)
            .exclude(id__in=used_ids)
            .select_related("topic", "topic__parent")
        )
        f = {
            "q": request.GET.get("q", "").strip(),
            "topic": request.GET.get("topic", ""),
            "item_type": request.GET.get("item_type", ""),
            "difficulty": request.GET.get("difficulty", ""),
        }
        if f["q"]:
            qs = qs.filter(Q(title__icontains=f["q"]) | Q(body__icontains=f["q"]))
        if f["topic"].isdigit():
            qs = qs.filter(topic_id=int(f["topic"]))
        if f["item_type"]:
            qs = qs.filter(item_type=f["item_type"])
        if f["difficulty"]:
            qs = qs.filter(difficulty=f["difficulty"])
        return render(
            request,
            "formatting/question_picker.html",
            {
                "section": section,
                "paper": paper,
                "items": qs[:200],
                "filters": f,
                "topics": Topic.objects.for_org(request.org).select_related("parent"),
                "difficulties": Item.Difficulty.choices,
            },
        )

    def post(self, request, pk):
        section = _get_section(request, pk)
        paper = section.paper
        ids = request.POST.getlist("item_ids")
        used_ids = set(
            PaperQuestion.objects.filter(section__paper=paper).values_list("item_id", flat=True)
        )
        # Only this org's active items; silently drop anything else (can only
        # happen via a tampered request).
        items = Item.objects.for_org(request.org).exclude(
            status=Item.Status.ARCHIVED
        ).filter(id__in=[i for i in ids if str(i).isdigit()])
        added = 0
        next_order = section.questions.count()
        for item in items:
            if item.id in used_ids:
                continue
            PaperQuestion.objects.create(section=section, item=item, order=next_order)
            next_order += 1
            added += 1
        messages.success(request, f"Added {added} question{'s' if added != 1 else ''}.")
        return redirect("formatting:paper_detail", pk=paper.pk)


class PaperQuestionRemoveView(FormattingMixin, View):
    def post(self, request, pk):
        pq = get_object_or_404(
            PaperQuestion.objects.select_related("section__paper"),
            pk=pk,
            section__paper__org=request.org,
        )
        paper_pk = pq.section.paper_id
        pq.delete()
        return redirect("formatting:paper_detail", pk=paper_pk)


class PaperQuestionMoveView(FormattingMixin, View):
    def post(self, request, pk):
        pq = get_object_or_404(
            PaperQuestion.objects.select_related("section__paper"),
            pk=pk,
            section__paper__org=request.org,
        )
        _move(pq.section.questions.all(), pq, request.POST.get("direction", ""))
        return redirect("formatting:paper_detail", pk=pq.section.paper_id)


class PaperQuestionMarksView(FormattingMixin, View):
    def post(self, request, pk):
        pq = get_object_or_404(
            PaperQuestion.objects.select_related("section__paper"),
            pk=pk,
            section__paper__org=request.org,
        )
        raw = request.POST.get("marks_override", "").strip()
        if raw == "":
            pq.marks_override = None
        else:
            try:
                value = Decimal(raw)
                if value < 0:
                    raise InvalidOperation
                pq.marks_override = value
            except InvalidOperation:
                messages.error(request, "Marks must be a non-negative number.")
                return redirect("formatting:paper_detail", pk=pq.section.paper_id)
        pq.save(update_fields=["marks_override"])
        return redirect("formatting:paper_detail", pk=pq.section.paper_id)


class PaperPdfView(FormattingMixin, View):
    answers = False

    def get(self, request, pk):
        paper = _get_paper(request, pk)
        data = pdf.build_paper_pdf(paper, answers=self.answers)
        suffix = "marking-scheme" if self.answers else "question-paper"
        filename = f"{paper.title[:60].strip().replace(' ', '-') or 'paper'}-{suffix}.pdf"
        response = HttpResponse(data, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{filename}"'
        return response


class TemplateListView(FormattingMixin, View):
    def get(self, request):
        return render(
            request,
            "formatting/template_list.html",
            {"templates": PaperTemplate.objects.for_org(request.org)},
        )


class TemplateCreateView(FormattingMixin, View):
    def get(self, request):
        return render(
            request,
            "formatting/template_form.html",
            {"form": PaperTemplateForm(), "is_new": True},
        )

    def post(self, request):
        form = PaperTemplateForm(request.POST)
        if not form.is_valid():
            return render(
                request, "formatting/template_form.html", {"form": form, "is_new": True}
            )
        template = form.save(commit=False)
        template.org = request.org
        template.save()
        messages.success(request, f"Template “{template.name}” saved.")
        return redirect("formatting:template_list")


class TemplateUpdateView(FormattingMixin, View):
    def get(self, request, pk):
        template = get_object_or_404(PaperTemplate.objects.for_org(request.org), pk=pk)
        return render(
            request,
            "formatting/template_form.html",
            {"form": PaperTemplateForm(instance=template), "is_new": False},
        )

    def post(self, request, pk):
        template = get_object_or_404(PaperTemplate.objects.for_org(request.org), pk=pk)
        form = PaperTemplateForm(request.POST, instance=template)
        if not form.is_valid():
            return render(
                request, "formatting/template_form.html", {"form": form, "is_new": False}
            )
        form.save()
        messages.success(request, "Template updated.")
        return redirect("formatting:template_list")


class TemplateDeleteView(FormattingMixin, View):
    def post(self, request, pk):
        template = get_object_or_404(PaperTemplate.objects.for_org(request.org), pk=pk)
        n = template.papers.count()
        template.delete()
        note = f" ({n} paper{'s' if n != 1 else ''} now use the built-in default)" if n else ""
        messages.success(request, f"Template deleted{note}.")
        return redirect("formatting:template_list")
