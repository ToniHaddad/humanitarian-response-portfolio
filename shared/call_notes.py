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

# Templates the model may train on. HELD-OUT templates (below) appear only in the test split, so the
# test measures phrasing the model never saw. Both groups use the same category vocabulary on purpose.
_EN_TRAIN: dict[str, list[str]] = {
    "food": [
        "Caller says the family of {n} has no food left since {when}.",
        "Mother asking for a food parcel, {n} children at home, nothing to cook.",
        "Family ran out of rice and flour {when}, asking when the next distribution is.",
        "Elderly man alone, cannot buy groceries, asks for food support.",
        "Household of {n} skipped meals {when}, requesting food assistance.",
        "Caller asks to be added to the food parcel list for {area}.",
        "They need baby formula and food for {n} people.",
        "Children are hungry, the pantry is empty, asking for a ration of oil, sugar and lentils.",
        "Family cannot afford groceries any more, asking for a monthly food basket.",
        "Asking when the next food parcel distribution is, the last parcel did not cover the month.",
    ],
    "shelter": [
        "Family displaced {when}, sleeping in a car, needs a place to stay.",
        "Roof leaking badly after the storm, {n} people in one room, needs shelter repair.",
        "Caller evicted {when}, asks about collective shelter in {area}.",
        "Need mattresses and blankets, house damaged, {n} people sleeping on the floor.",
        "Windows broken and no door, cold at night, requesting shelter kit.",
        "Family of {n} staying in an unfinished building, needs safe housing.",
        "Tent torn by the wind, family of {n} needs a new tent and plastic sheeting.",
        "Walls cracked and the ceiling is unsafe, the family needs another house to live in.",
        "Looking for a room or temporary housing for {n} people, currently homeless.",
    ],
    "health": [
        "Child with high fever since {when}, family cannot reach a clinic.",
        "Diabetic father ran out of insulin, needs medication urgently.",
        "Pregnant woman needs a check-up, no transport to the health centre.",
        "Caller asks for chronic disease medicines for her mother.",
        "Injured leg {when}, wound not healing, asks for a doctor.",
        "Elderly woman with heart condition needs her prescription refilled.",
        "Man needs dialysis sessions at the hospital, cannot pay for the treatment.",
        "Daughter has asthma and no inhaler, the pharmacy has none, needs medicine.",
        "Sick child needs a vaccination, asks where the nearest health centre or doctor is.",
        "Patient needs surgery and a hospital bed, cannot afford the medical bills and medicines.",
    ],
    "cash": [
        "Family cannot pay rent this month, asking about cash assistance.",
        "Caller lost his job {when}, needs money for bills and debts.",
        "Asking if they qualify for the monthly cash transfer, {n} people in household.",
        "Need cash to pay for school fees and transport.",
        "Widow with {n} children has no income, requests financial help.",
        "Asking why the cash card was not topped up {when}.",
        "Salary stopped, cannot cover bills, asks to register for cash support.",
        "Asking how to receive the cash grant, the amount did not arrive on the card this month.",
        "Family has debts and no savings, needs financial assistance to pay the landlord.",
    ],
    "water_hygiene": [
        "No water in the building since {when}, need drinking water.",
        "Water tank broken, {n} people without clean water.",
        "Asking for hygiene kits, soap and sanitary pads for the family.",
        "Toilet blocked and sewage leaking, children getting sick.",
        "Drinking water tastes bad, family asks for water filters.",
        "Need diapers and hygiene items for {n} people.",
        "Tap water is cloudy and dirty, asks for chlorine tablets and a clean water tank.",
        "Needs soap, shampoo, toothpaste and a hygiene kit for {n} people.",
        "No latrine or washing facilities in the camp, asks for toilets and showers.",
    ],
    "protection": [
        "Caller afraid for her safety at home, asks to speak to someone privately.",
        "Child not attending school and working on the street, mother asks for help.",
        "Woman reports harassment at the shelter, wants support.",
        "Lost identity documents {when}, worried about checkpoints.",
        "Elderly man left alone by family, needs someone to check on him.",
        "Family separated during displacement {when}, looking for a missing son.",
        "Teenage girl is being pressured to leave school, mother worried and asks for child protection support.",
        "Caller was threatened and beaten at home, afraid to go back, asks for a safe place and counselling.",
        "Family has no legal papers and fears detention, asks for legal aid.",
    ],
}

_EN_HELD: dict[str, list[str]] = {
    "food": [
        "No bread or canned food at home, family of {n} hungry.",
        "Kitchen is bare, nothing to eat tonight, wants dry rations or hot meals for {n} people.",
    ],
    "shelter": [
        "Landlord asked them to leave {when}, nowhere to go.",
        "Home was destroyed, the family has no roof and needs accommodation.",
        "Living in a garage with no heating, asks for insulation materials and a safe place to sleep.",
    ],
    "health": [
        "Baby coughing for days, mother asks where to find a free clinic in {area}.",
        "Severe pain in the chest since {when}, needs a doctor to examine him and a prescription.",
    ],
    "cash": [
        "Debt at the shop is growing, family needs cash support.",
        "No income since the factory closed, requests a monthly money transfer for {n} people.",
        "Needs funds to pay the electricity and generator subscription, cannot afford it.",
    ],
    "water_hygiene": [
        "Water truck did not come to {area} {when}.",
        "Water pump broke, no clean water for drinking or washing {when}.",
        "Running out of washing powder, soap and menstrual hygiene products, asks for a hygiene parcel.",
    ],
    "protection": [
        "Caller reports threats from a neighbour, asks for advice.",
        "Boy was approached by an armed group, the family is scared and wants protection advice.",
        "Man with a disability living alone is being abused by relatives, needs a social worker to visit.",
    ],
}

_AR_TRAIN: dict[str, list[str]] = {
    "food": [
        "المتصل يقول إن العائلة المكونة من {n} أشخاص ليس لديها طعام منذ {when_ar}.",
        "أم تطلب حصة غذائية، لديها {n} أطفال ولا يوجد شيء للطبخ.",
        "نفد الأرز والطحين {when_ar}، يسألون عن موعد التوزيع القادم.",
        "رجل مسن يعيش وحده ولا يستطيع شراء الطعام، يطلب مساعدة غذائية.",
        "عائلة من {n} أفراد لم تأكل جيدا {when_ar}، تطلب مساعدة غذائية.",
        "يطلب إضافة اسمه إلى لائحة الحصص الغذائية في {area_ar}.",
        "بحاجة إلى حليب أطفال وطعام لـ {n} أشخاص.",
        "الأطفال جائعون والخزانة فارغة، يطلبون حصة من الزيت والسكر والعدس.",
        "العائلة لم تعد تستطيع شراء المواد الغذائية، تطلب سلة غذائية شهرية.",
        "يسأل متى يكون توزيع الحصص الغذائية القادم، فالحصة الأخيرة لم تكفِ الشهر.",
    ],
    "shelter": [
        "العائلة نزحت {when_ar} وتنام في السيارة، بحاجة إلى مكان للإقامة.",
        "السقف يدلف بعد العاصفة، {n} أشخاص في غرفة واحدة، بحاجة إلى ترميم.",
        "تم إخلاء المتصل {when_ar}، يسأل عن مركز إيواء في {area_ar}.",
        "بحاجة إلى فرش وبطانيات، البيت متضرر و{n} أشخاص ينامون على الأرض.",
        "النوافذ مكسورة ولا يوجد باب، البرد شديد في الليل، يطلب مواد إيواء.",
        "عائلة من {n} أفراد تسكن في مبنى غير مكتمل، بحاجة إلى سكن آمن.",
        "الخيمة تمزقت بسبب الريح، عائلة من {n} أفراد بحاجة إلى خيمة جديدة ونايلون.",
        "الجدران متصدعة والسقف غير آمن، العائلة بحاجة إلى بيت آخر تسكن فيه.",
        "يبحثون عن غرفة للإيجار أو سكن مؤقت لـ {n} أشخاص، وهم بلا مأوى حاليا.",
    ],
    "health": [
        "طفل لديه حرارة مرتفعة منذ {when_ar}، العائلة لا تستطيع الوصول إلى مستوصف.",
        "الأب مريض سكري ونفد الأنسولين، بحاجة إلى دواء بشكل عاجل.",
        "امرأة حامل بحاجة إلى معاينة ولا يوجد نقل إلى المركز الصحي.",
        "المتصلة تطلب أدوية أمراض مزمنة لوالدتها.",
        "إصابة في الرجل {when_ar} والجرح لا يلتئم، يطلب طبيبا.",
        "امرأة مسنة مريضة قلب تحتاج إلى تجديد الوصفة الطبية.",
        "رجل يحتاج إلى جلسات غسيل كلى في المستشفى ولا يستطيع دفع كلفة العلاج.",
        "البنت مصابة بالربو وليس لديها بخاخ، الصيدلية لا تملكه، تحتاج إلى دواء.",
        "طفل مريض يحتاج إلى لقاح، تسأل أين أقرب مركز صحي أو طبيب.",
        "مريض يحتاج إلى عملية جراحية وسرير في المستشفى ولا يستطيع دفع الفواتير الطبية والأدوية.",
    ],
    "cash": [
        "العائلة لا تستطيع دفع الإيجار هذا الشهر، تسأل عن مساعدة نقدية.",
        "المتصل خسر عمله {when_ar} ويحتاج إلى مال للفواتير والديون.",
        "يسأل إن كان مؤهلا للمساعدة النقدية الشهرية، عدد أفراد الأسرة {n}.",
        "بحاجة إلى مال لدفع أقساط المدرسة والمواصلات.",
        "أرملة لديها {n} أطفال بلا دخل، تطلب مساعدة مالية.",
        "يسأل لماذا لم تشحن البطاقة النقدية {when_ar}.",
        "توقف الراتب ولا تستطيع تغطية الفواتير، تطلب التسجيل للدعم النقدي.",
        "يسأل كيف يستلم المنحة النقدية، فالمبلغ لم يصل إلى البطاقة هذا الشهر.",
        "على العائلة ديون ولا توجد مدخرات، بحاجة إلى مساعدة مالية لدفع الإيجار لصاحب البيت.",
    ],
    "water_hygiene": [
        "لا توجد مياه في المبنى منذ {when_ar}، بحاجة إلى مياه شرب.",
        "خزان المياه مكسور، {n} أشخاص بلا مياه نظيفة.",
        "يطلبون حصص نظافة وصابون وفوط صحية للعائلة.",
        "المرحاض مسدود والصرف الصحي يتسرب، الأطفال يمرضون.",
        "طعم مياه الشرب سيئ، العائلة تطلب فلاتر مياه.",
        "بحاجة إلى حفاضات ومواد نظافة لـ {n} أشخاص.",
        "مياه الصنبور عكرة ووسخة، يطلبون أقراص كلور وخزان مياه نظيفا.",
        "بحاجة إلى صابون وشامبو ومعجون أسنان وحقيبة نظافة لـ {n} أشخاص.",
        "لا يوجد مرحاض ولا أماكن للاستحمام في المخيم، يطلبون دورات مياه ومرشات.",
    ],
    "protection": [
        "المتصلة خائفة على سلامتها في البيت، تطلب التحدث مع أحد بشكل خاص.",
        "الطفل لا يذهب إلى المدرسة ويعمل في الشارع، الأم تطلب المساعدة.",
        "امرأة تبلغ عن تحرش في مركز الإيواء وتريد الدعم.",
        "فقد أوراقه الثبوتية {when_ar} وهو قلق من الحواجز.",
        "رجل مسن تركته عائلته وحده، بحاجة إلى من يطمئن عليه.",
        "العائلة تفرقت أثناء النزوح {when_ar}، يبحثون عن ابنهم المفقود.",
        "مراهقة تتعرض لضغط لترك المدرسة والأم قلقة وتطلب دعما لحماية الطفلة.",
        "المتصلة تعرضت للتهديد والضرب في البيت وتخاف العودة، تطلب مكانا آمنا ودعما نفسيا.",
        "العائلة بلا أوراق قانونية وتخشى التوقيف، تطلب مساعدة قانونية.",
    ],
}

_AR_HELD: dict[str, list[str]] = {
    "food": [
        "لا يوجد خبز ولا معلبات في البيت، العائلة جائعة.",
        "المطبخ فارغ ولا يوجد ما يؤكل الليلة، يريدون مؤونة جافة أو وجبات ساخنة لـ {n} أشخاص.",
    ],
    "shelter": [
        "طلب منهم صاحب البيت المغادرة {when_ar}، ولا مكان يذهبون إليه.",
        "البيت تهدم والعائلة بلا سقف وتحتاج إلى مكان للإقامة.",
        "يسكنون في مرآب بلا تدفئة، يطلبون مواد عزل ومكانا آمنا للنوم.",
    ],
    "health": [
        "الطفل يسعل منذ أيام، الأم تسأل عن عيادة مجانية في {area_ar}.",
        "ألم شديد في الصدر منذ {when_ar}، يحتاج إلى طبيب يعاينه ووصفة دواء.",
    ],
    "cash": [
        "الدين عند الدكان يكبر، العائلة بحاجة إلى دعم نقدي.",
        "لا دخل منذ أغلق المصنع، يطلب تحويلا نقديا شهريا لـ {n} أشخاص.",
        "بحاجة إلى مال لدفع اشتراك الكهرباء والمولد ولا تستطيع تحمل الكلفة.",
    ],
    "water_hygiene": [
        "صهريج المياه لم يأت إلى {area_ar} {when_ar}.",
        "مضخة المياه تعطلت ولا توجد مياه نظيفة للشرب أو الغسيل منذ {when_ar}.",
        "نفد مسحوق الغسيل والصابون ومستلزمات النظافة الشخصية للنساء، يطلبون حصة نظافة.",
    ],
    "protection": [
        "المتصل يبلغ عن تهديدات من جار ويطلب النصيحة.",
        "اقترب مسلحون من الصبي والعائلة خائفة وتريد نصيحة للحماية.",
        "رجل من ذوي الإعاقة يعيش وحده ويتعرض للإساءة من أقاربه، يحتاج إلى زيارة من مرشد اجتماعي.",
    ],
}

# Notes for the seeded service requests and calls (`make_note`) use only the original templates: the
# first `_LEGACY_TRAIN_COUNT` training templates plus the first held-out one. Keeping that list (and so the
# random draws) unchanged keeps every other seeded table identical; the extra templates above only
# enrich the classifier's labelled notes.
_LEGACY_TRAIN_COUNT = {"food": 7, "shelter": 6, "health": 6, "cash": 6, "water_hygiene": 6, "protection": 6}
_EN: dict[str, list[str]] = {c: [*_EN_TRAIN[c][: _LEGACY_TRAIN_COUNT[c]], _EN_HELD[c][0]] for c in CATEGORIES}
_AR: dict[str, list[str]] = {c: [*_AR_TRAIN[c][: _LEGACY_TRAIN_COUNT[c]], _AR_HELD[c][0]] for c in CATEGORIES}

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
    held_out_share: float = 0.15,
    test_size: int = 217,
) -> list[tuple[str, str, str, str]]:
    """(text, language, label, split) tuples, half Arabic and half English per category.

    About `held_out_share` of the notes use a HELD-OUT template (see `_EN_HELD`, `_AR_HELD`) and go only to
    the test split; the model never sees that phrasing in training, so the test also measures unseen
    phrasing (otherwise every test note shares a template with training notes and accuracy is a
    meaningless 100%). The rest are drawn from the training templates; just enough of them are added to
    the test split to make it exactly `test_size` notes, the others are the training split.
    """
    rows: list[tuple[str, str, str, bool]] = []
    for category in CATEGORIES:
        for i in range(per_category):
            language = "ar" if i % 2 else "en"
            held = rng.random() < held_out_share
            pool = (_AR_HELD if language == "ar" else _EN_HELD) if held else (_AR_TRAIN if language == "ar" else _EN_TRAIN)
            text = rng.choice(pool[category]).format(
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
            rows.append((text, language, category, held))
    rng.shuffle(rows)
    need = max(0, test_size - sum(1 for r in rows if r[3]))
    out = []
    for text, language, category, held in rows:
        if held:
            split = "test"
        elif need > 0:
            split, need = "test", need - 1
        else:
            split = "train"
        out.append((text, language, category, split))
    return out
