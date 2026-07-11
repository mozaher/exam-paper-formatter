class ActiveOrganizationMiddleware:
    """Attaches ``request.org`` and ``request.membership`` for signed-in users.

    v1 assumes a user belongs to one organization (the first membership by
    id). A session-based org switcher can be layered on later without
    changing downstream code, which only reads request.org.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.org = None
        request.membership = None
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            membership = (
                user.memberships.select_related("org").order_by("id").first()
            )
            if membership is not None:
                request.membership = membership
                request.org = membership.org
        return self.get_response(request)
