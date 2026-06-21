"""In-memory cake recipe fixtures (stands in for a real datastore/API)."""

from __future__ import annotations

from mcp_template.models import Recipe

RECIPES: dict[str, Recipe] = {
    "classic-sponge": Recipe(
        name="classic-sponge",
        layers=2,
        servings=8,
        ingredients=[
            "200g self-raising flour",
            "200g caster sugar",
            "200g butter, softened",
            "4 eggs",
            "1 tsp vanilla extract",
        ],
        steps=[
            "Cream butter and sugar until pale.",
            "Beat in eggs one at a time, then vanilla.",
            "Fold in the flour.",
            "Divide between two tins and bake.",
            "Cool, then sandwich with jam and cream.",
        ],
        bake_minutes=25,
        temp_c=180,
    ),
    "chocolate-fudge": Recipe(
        name="chocolate-fudge",
        layers=3,
        servings=12,
        ingredients=[
            "250g plain flour",
            "300g caster sugar",
            "85g cocoa powder",
            "3 eggs",
            "200ml buttermilk",
            "150ml vegetable oil",
        ],
        steps=[
            "Whisk dry ingredients together.",
            "Add eggs, buttermilk and oil; beat until smooth.",
            "Divide between three tins and bake.",
            "Cool, then stack with chocolate ganache.",
        ],
        bake_minutes=35,
        temp_c=170,
    ),
    "lemon-drizzle": Recipe(
        name="lemon-drizzle",
        layers=1,
        servings=10,
        ingredients=[
            "225g self-raising flour",
            "225g caster sugar",
            "225g butter, softened",
            "4 eggs",
            "Zest and juice of 2 lemons",
        ],
        steps=[
            "Cream butter and sugar; beat in eggs and zest.",
            "Fold in flour and spoon into a loaf tin.",
            "Bake, then prick and pour over lemon-sugar drizzle.",
        ],
        bake_minutes=45,
        temp_c=180,
    ),
}


def recipe_names() -> list[str]:
    return sorted(RECIPES)
