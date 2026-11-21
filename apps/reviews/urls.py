from django.urls import path

from apps.reviews import views

app_name = "reviews"

urlpatterns = [
    # 管理员
    path("", views.board, name="board"),
    path("rounds/<uuid:round_id>/", views.round_detail, name="round_detail"),
    path("rounds/<uuid:round_id>/assign/", views.assign, name="assign"),
    path("rounds/<uuid:round_id>/confirm-unanimous/", views.confirm_unanimous, name="confirm_unanimous"),
    path("rounds/<uuid:round_id>/finalize/", views.finalize, name="finalize"),
    path("rounds/<uuid:round_id>/extend-rectify/", views.extend_rectify, name="extend_rectify"),
    path("round-items/<int:round_item_id>/rule/", views.rule, name="rule"),
    path("assignments/<int:assignment_id>/reassign/", views.reassign, name="reassign"),
    path("assignments/<int:assignment_id>/remind/", views.remind, name="remind"),
    path("assignments/<int:assignment_id>/extend/", views.extend, name="extend"),
    path("assessments/<uuid:assessment_id>/start/", views.start_review, name="start"),
    # 企业与管理员
    path("assessments/<uuid:assessment_id>/history/", views.history, name="history"),
    # 专家
    path("tasks/", views.task_list, name="task_list"),
    path("tasks/<int:assignment_id>/", views.task_detail, name="task_detail"),
    path("tasks/<int:assignment_id>/respond/", views.task_respond, name="task_respond"),
    path("tasks/<int:assignment_id>/save/", views.task_save, name="task_save"),
    path("tasks/<int:assignment_id>/submit/", views.task_submit, name="task_submit"),
    path("tasks/<int:assignment_id>/evidence/<uuid:evidence_id>/", views.task_evidence, name="task_evidence"),
]
