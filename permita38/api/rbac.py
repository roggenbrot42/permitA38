"""How routes declare the rights they need, in a form tests (and documentation) can enumerate."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Rbac:
    """`all_of`: every one of these rights is needed. `any_of`: at least one of these is needed (if given)."""

    all_of: tuple[str, ...] = ()
    any_of: tuple[str, ...] = ()

    def allows(self, held) -> bool:
        return all(r in held for r in self.all_of) and (not self.any_of or any(r in held for r in self.any_of))


def declared(route) -> list[Rbac]:
    """The rights declarations found in a route's dependency tree (usually zero or one)."""
    found: list[Rbac] = []

    def walk(dep):
        spec = getattr(dep.call, "rbac", None)
        if isinstance(spec, Rbac):
            found.append(spec)
        for sub in dep.dependencies:
            walk(sub)

    walk(route.dependant)
    return found
