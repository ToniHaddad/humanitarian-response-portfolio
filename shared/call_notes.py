"""Synthetic Arabic and English call notes for the six request categories.

Used by the seed script (service requests, calls) and by the Project 03 classifier
(labelled training and test notes). Deterministic given a random.Random.
A small share of notes mention two needs; their label is the first need, which
gives the classifier realistic, honest mistakes to show.
"""

from __future__ import annotations

import random

CATEGORIES: tuple[str, ...] = ("food", "shelter", "health", "cash", "water_hygiene", "protection")

CATEGORY_LABELS: dict[str, str] = {
    "food": "Food",
    "shelter": "Shelter",
    "health": "Health",
    "cash": "Cash assistance",
    "water_hygiene": "Water and hygiene",
    "protection": "Protection",
}

_EN: dict[str, list[str]] = {
    "food": [
        "Caller says the family of {n} has no food left since {when}.",
        "Mother asking for a food parcel, {n} children at home, nothing to cook.",
        "Family ran out of rice and flour {when}, asking when the next distribution is.",
        "Elderly man alone, cannot buy groceries, asks for food support.",
        "Household of {n} skipped meals {when}, requesting food assistance.",
        "Caller asks to be added to the food parcel list for {area}.",
        "They need baby formula and food for {n} people.",
        "No bread or canned food at home, family of {n} hungry.",
    ],
    "shelter": [
        "Family displaced {when}, sleeping in a car, needs a place to stay.",
        "Roof leaking badly after the storm, {n} people in one room, needs shelter repair.",
        "Caller evicted {when}, asks about collective shelter in {area}.",
        "Need mattresses and blankets, house damaged, {n} people sleeping on the floor.",
        "Windows broken and no door, cold at night, requesting shelter kit.",
        "Family of {n} staying in an unfinished building, needs safe housing.",
        "Landlord asked them to leave {when}, nowhere to go.",
    ],
    "health": [
        "Child with high fever since {when}, family cannot reach a clinic.",
        "Diabetic father ran out of insulin, needs medication urgently.",
        "Pregnant woman needs a check-up, no transport to the health centre.",
        "Caller asks for chronic disease medicines for her mother.",
        "Injured leg {when}, wound not healing, asks for a doctor.",
        "Elderly woman with heart condition needs her prescription refilled.",
        "Baby coughing for days, mother asks where to find a free clinic in {area}.",
    ],
    "cash": [
        "Family cannot pay rent this month, asking about cash assistance.",
        "Caller lost his job {when}, needs money for bills and debts.",
        "Asking if they qualify for the monthly cash transfer, {n} people in household.",
        "Need cash to pay for school fees and transport.",
        "Widow with {n} children has no income, requests financial help.",
        "Asking why the cash card was not topped up {when}.",
        "Debt at the shop is growing, family needs cash support.",
    ],
    "water_hygiene": [
        "No water in the building since {when}, need drinking water.",
        "Water tank broken, {n} people without clean water.",
        "Asking for hygiene kits, soap and sanitary pads for the family.",
        "Toilet blocked and sewage leaking, children getting sick.",
        "Drinking water tastes bad, family asks for water filters.",
        "Need diapers and hygiene items for {n} people.",
        "Water truck did not come to {area} {when}.",
    ],
    "protection": [
        "Caller afraid for her safety at home, asks to speak to someone privately.",
        "Child not attending school and working on the street, mother asks for help.",
        "Woman reports harassment at the shelter, wants support.",
        "Lost identity documents {when}, worried about checkpoints.",
        "Elderly man left alone by family, needs someone to check on him.",
        "Family separated during displacement {when}, looking for a missing son.",
        "Caller reports threats from a neighbour, asks for advice.",
    ],
}

_AR: dict[str, list[str]] = {
    "food": [
        "المتصل يقول إن العائلة المكونة من {n} أشخاص ليس لديها طعام منذ {when_ar}.",
        "أم تطلب حصة غذائية، لديها {n} أطفال ولا يوجد شيء للطبخ.",
        "نفد الأرز والطحين {when_ar}، يسألون عن موعد التوزيع القادم.",
        "رجل مسن يعيش وحده ولا يستطيع شراء الطعام، يطلب مساعدة غذائية.",
        "عائلة من {n} أفراد لم تأكل جيدا {when_ar}، تطلب مساعدة غذائية.",
        "يطلب إضافة اسمه إلى لائحة الحصص الغذائية في {area_ar}.",
        "بحاجة إلى حليب أطفال وطعام لـ {n} أشخاص.",
        "لا يوجد خبز ولا معلبات في البيت، العائلة جائعة.",
    ],
    "shelter": [
        "العائلة نزحت {when_ar} وتنام في السيارة، بحاجة إلى مكان للإقامة.",
        "السقف يدلف بعد العاصفة، {n} أشخاص في غرفة واحدة، بحاجة إلى ترميم.",
        "تم إخلاء المتصل {when_ar}، يسأل عن مركز إيواء في {area_ar}.",
        "بحاجة إلى فرش وبطانيات، البيت متضرر و{n} أشخاص ينامون على الأرض.",
        "النوافذ مكسورة ولا يوجد باب، البرد شديد في الليل، يطلب مواد إيواء.",
        "عائلة من {n} أفراد تسكن في مبنى غير مكتمل، بحاجة إلى سكن آمن.",
        "طلب منهم صاحب البيت المغادرة {when_ar}، ولا مكان يذهبون إليه.",
    ],
    "health": [
        "طفل لديه حرارة مرتفعة منذ {when_ar}، العائلة لا تستطيع الوصول إلى مستوصف.",
        "الأب مريض سكري ونفد الأنسولين، بحاجة إلى دواء بشكل عاجل.",
        "امرأة حامل بحاجة إلى معاينة ولا يوجد نقل إلى المركز الصحي.",
        "المتصلة تطلب أدوية أمراض مزمنة لوالدتها.",
        "إصابة في الرجل {when_ar} والجرح لا يلتئم، يطلب طبيبا.",
        "امرأة مسنة مريضة قلب تحتاج إلى تجديد الوصفة الطبية.",
        "الطفل يسعل منذ أيام، الأم تسأل عن عيادة مجانية في {area_ar}.",
    ],
    "cash": [
        "العائلة لا تستطيع دفع الإيجار هذا الشهر، تسأل عن مساعدة نقدية.",
        "المتصل خسر عمله {when_ar} ويحتاج إلى مال للفواتير والديون.",
        "يسأل إن كان مؤهلا للمساعدة النقدية الشهرية، عدد أفراد الأسرة {n}.",
        "بحاجة إلى مال لدفع أقساط المدرسة والمواصلات.",
        "أرملة لديها {n} أطفال بلا دخل، تطلب مساعدة مالية.",
        "يسأل لماذا لم تشحن البطاقة النقدية {when_ar}.",
        "الدين عند الدكان يكبر، العائلة بحاجة إلى دعم نقدي.",
    ],
    "water_hygiene": [
        "لا توجد مياه في المبنى منذ {when_ar}، بحاجة إلى مياه شرب.",
        "خزان المياه مكسور، {n} أشخاص بلا مياه نظيفة.",
        "يطلبون حصص نظافة وصابون وفوط صحية للعائلة.",
        "المرحاض مسدود والصرف الصحي يتسرب، الأطفال يمرضون.",
        "طعم مياه الشرب سيئ، العائلة تطلب فلاتر مياه.",
        "بحاجة إلى حفاضات ومواد نظافة لـ {n} أشخاص.",
        "صهريج المياه لم يأت إلى {area_ar} {when_ar}.",
    ],
    "protection": [
        "المتصلة خائفة على سلامتها في البيت، تطلب التحدث مع أحد بشكل خاص.",
        "الطفل لا يذهب إلى المدرسة ويعمل في الشارع، الأم تطلب المساعدة.",
        "امرأة تبلغ عن تحرش في مركز الإيواء وتريد الدعم.",
        "فقد أوراقه الثبوتية {when_ar} وهو قلق من الحواجز.",
        "رجل مسن تركته عائلته وحده، بحاجة إلى من يطمئن عليه.",
        "العائلة تفرقت أثناء النزوح {when_ar}، يبحثون عن ابنهم المفقود.",
        "المتصل يبلغ عن تهديدات من جار ويطلب النصيحة.",
    ],
}

_WHEN_EN = ["yesterday", "two days ago", "last week", "this morning", "three days ago", "the weekend"]
_WHEN_AR = ["أمس", "يومين", "الأسبوع الماضي", "صباح اليوم", "ثلاثة أيام", "نهاية الأسبوع"]

# Short second-need fragments used to make some notes ambiguous.
_SECOND_EN: dict[str, str] = {
    "food": "also short of food",
    "shelter": "and the house is damaged",
    "health": "and the child is sick",
    "cash": "and cannot pay rent",
    "water_hygiene": "and there is no water",
    "protection": "and she feels unsafe",
}
_SECOND_AR: dict[str, str] = {
    "food": "وأيضا لا يوجد طعام",
    "shelter": "والبيت متضرر",
    "health": "والطفل مريض",
    "cash": "ولا يستطيعون دفع الإيجار",
    "water_hygiene": "ولا توجد مياه",
    "protection": "وهي لا تشعر بالأمان",
}


def make_note(
    rng: random.Random,
    category: str,
    language: str,
    area_en: str = "the area",
    area_ar: str = "المنطقة",
    ambiguous: bool = False,
) -> str:
    """One call note for a category in 'en' or 'ar'."""
    templates = _EN[category] if language == "en" else _AR[category]
    text = rng.choice(templates).format(
        n=rng.randint(2, 9),
        when=rng.choice(_WHEN_EN),
        when_ar=rng.choice(_WHEN_AR),
        area=area_en,
        area_ar=area_ar,
    )
    if ambiguous:
        other = rng.choice([c for c in CATEGORIES if c != category])
        extra = _SECOND_EN[other] if language == "en" else _SECOND_AR[other]
        text = text.rstrip(".") + (", " if language == "en" else "، ") + extra + "."
    return text


def make_labelled_notes(rng: random.Random, per_category: int = 100, ambiguous_share: float = 0.12) -> list[tuple[str, str, str]]:
    """(text, language, label) tuples, half Arabic and half English per category."""
    rows: list[tuple[str, str, str]] = []
    for category in CATEGORIES:
        for i in range(per_category):
            language = "ar" if i % 2 else "en"
            ambiguous = rng.random() < ambiguous_share
            rows.append((make_note(rng, category, language, ambiguous=ambiguous), language, category))
    rng.shuffle(rows)
    return rows


def make_labelled_notes_split(
    rng: random.Random,
    per_category: int = 100,
    ambiguous_share: float = 0.12,
    held_out_templates: int = 1,
    test_every: int = 4,
) -> list[tuple[str, str, str, str]]:
    """(text, language, label, split) tuples, half Arabic and half English per category.

    The last `held_out_templates` templates of each category and language appear only in
    the test split, so the test also measures phrasing the model never saw in training
    (otherwise every test note shares a template with training notes and accuracy is a
    meaningless 100%). The other notes go to the test split one time in `test_every`.
    """
    rows: list[tuple[str, str, str, bool]] = []
    for category in CATEGORIES:
        for i in range(per_category):
            language = "ar" if i % 2 else "en"
            templates = _EN[category] if language == "en" else _AR[category]
            k = rng.randrange(len(templates))
            text = templates[k].format(
                n=rng.randint(2, 9),
                when=rng.choice(_WHEN_EN),
                when_ar=rng.choice(_WHEN_AR),
                area="the area",
                area_ar="المنطقة",
            )
            if rng.random() < ambiguous_share:
                other = rng.choice([c for c in CATEGORIES if c != category])
                extra = _SECOND_EN[other] if language == "en" else _SECOND_AR[other]
                text = text.rstrip(".") + (", " if language == "en" else "، ") + extra + "."
            rows.append((text, language, category, k >= len(templates) - held_out_templates))
    rng.shuffle(rows)
    return [(t, lang, c, "test" if held or n % test_every == 0 else "train") for n, (t, lang, c, held) in enumerate(rows)]
