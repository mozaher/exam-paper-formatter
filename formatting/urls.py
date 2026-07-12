from django.urls import path

from . import views

app_name = "formatting"

urlpatterns = [
    path("", views.PaperListView.as_view(), name="paper_list"),
    path("new/", views.PaperCreateView.as_view(), name="paper_create"),
    path("<int:pk>/", views.PaperDetailView.as_view(), name="paper_detail"),
    path("<int:pk>/edit/", views.PaperUpdateView.as_view(), name="paper_edit"),
    path("<int:pk>/delete/", views.PaperDeleteView.as_view(), name="paper_delete"),
    path("<int:pk>/sections/new/", views.SectionCreateView.as_view(), name="section_create"),
    path("<int:pk>/paper.pdf", views.PaperPdfView.as_view(answers=False), name="paper_pdf"),
    path(
        "<int:pk>/marking-scheme.pdf",
        views.PaperPdfView.as_view(answers=True),
        name="paper_marking_pdf",
    ),
    path("sections/<int:pk>/delete/", views.SectionDeleteView.as_view(), name="section_delete"),
    path("sections/<int:pk>/move/", views.SectionMoveView.as_view(), name="section_move"),
    path("sections/<int:pk>/add/", views.QuestionPickerView.as_view(), name="question_picker"),
    path("questions/<int:pk>/remove/", views.PaperQuestionRemoveView.as_view(), name="pq_remove"),
    path("questions/<int:pk>/move/", views.PaperQuestionMoveView.as_view(), name="pq_move"),
    path("questions/<int:pk>/marks/", views.PaperQuestionMarksView.as_view(), name="pq_marks"),
    path("templates/", views.TemplateListView.as_view(), name="template_list"),
    path("templates/new/", views.TemplateCreateView.as_view(), name="template_create"),
    path("templates/<int:pk>/edit/", views.TemplateUpdateView.as_view(), name="template_edit"),
    path("templates/<int:pk>/delete/", views.TemplateDeleteView.as_view(), name="template_delete"),
]
