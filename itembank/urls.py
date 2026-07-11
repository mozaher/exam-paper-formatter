from django.urls import path

from . import views

app_name = "itembank"

urlpatterns = [
    path("", views.ItemListView.as_view(), name="item_list"),
    path("new/", views.ItemCreateView.as_view(), name="item_create"),
    path("<int:pk>/", views.ItemDetailView.as_view(), name="item_detail"),
    path("<int:pk>/edit/", views.ItemUpdateView.as_view(), name="item_edit"),
    path("<int:pk>/archive/", views.ItemArchiveView.as_view(), name="item_archive"),
    path("<int:pk>/qti.xml", views.ItemQtiXmlView.as_view(), name="item_qti_xml"),
    path("topics/", views.TopicListView.as_view(), name="topic_list"),
    path("topics/<int:pk>/delete/", views.TopicDeleteView.as_view(), name="topic_delete"),
    path("export/", views.QtiExportView.as_view(), name="qti_export"),
    path("import/", views.QtiImportView.as_view(), name="qti_import"),
]
