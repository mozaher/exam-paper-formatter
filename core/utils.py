from django.utils.text import slugify


def unique_org_slug(name, *, model=None):
    """Derive a unique slug for a new organization."""
    if model is None:
        from .models import Organization as model
    base = slugify(name)[:56] or "org"
    slug = base
    n = 2
    while model.objects.filter(slug=slug).exists():
        slug = f"{base}-{n}"
        n += 1
    return slug
