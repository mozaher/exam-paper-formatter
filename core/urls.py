from django.urls import path

from . import views

urlpatterns = [
    path("", views.DashboardView.as_view(), name="dashboard"),
    path("signup/", views.SignupView.as_view(), name="signup"),
    path("org/", views.OrgSettingsView.as_view(), name="org_settings"),
    path("org/invite/", views.InviteCreateView.as_view(), name="org_invite"),
    path("org/members/<int:pk>/remove/", views.MemberRemoveView.as_view(), name="org_member_remove"),
    path("invites/accept/<str:token>/", views.InviteAcceptView.as_view(), name="invite_accept"),
    path("billing/", views.BillingView.as_view(), name="billing"),
]
