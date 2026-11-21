"""Fixed fictional content for the seed script: places, names, providers, text templates.

Everything here is invented. Area and site names describe an imaginary region; the
grid coordinates (x_km, y_km) are on an invented 100 x 100 km plane.
"""

from __future__ import annotations

# (area_id, name_en, name_ar, district, x_km, y_km, population_estimate)
AREAS: list[tuple[str, str, str, str, float, float, int]] = [
    ("AR-01", "Cedarbrook", "جدول الأرز", "Highland District", 18.0, 82.0, 14200),
    ("AR-02", "Olive Terrace", "مدرجات الزيتون", "Highland District", 34.0, 74.0, 11800),
    ("AR-03", "Stonebridge", "الجسر الحجري", "Highland District", 52.0, 86.0, 9600),
    ("AR-04", "Harbor Flats", "سهول المرفأ", "Coastal District", 8.0, 46.0, 21500),
    ("AR-05", "Northfield", "الحقل الشمالي", "North District", 70.0, 92.0, 8700),
    ("AR-06", "Juniper Hill", "تلة العرعر", "North District", 84.0, 76.0, 7300),
    ("AR-07", "Saltmarsh", "المستنقع المالح", "Coastal District", 14.0, 24.0, 12900),
    ("AR-08", "Riverbend", "منعطف النهر", "Central District", 42.0, 50.0, 18400),
    ("AR-09", "Almond Grove", "بستان اللوز", "Central District", 60.0, 58.0, 10100),
    ("AR-10", "Eastgate", "البوابة الشرقية", "North District", 88.0, 48.0, 9900),
    ("AR-11", "Pinecrest", "قمة الصنوبر", "Central District", 66.0, 30.0, 8200),
    ("AR-12", "Lantern Bay", "خليج الفانوس", "Coastal District", 30.0, 12.0, 15600),
]

# (site_id, name, name_ar, area_id, site_type, dx, dy) offsets from the area centroid
SITES: list[tuple[str, str, str, str, str, float, float]] = [
    ("SITE-01", "Riverbend Distribution Point", "نقطة توزيع منعطف النهر", "AR-08", "distribution_point", 1.5, -1.0),
    ("SITE-02", "Harbor Flats Distribution Point", "نقطة توزيع سهول المرفأ", "AR-04", "distribution_point", 2.0, 1.0),
    ("SITE-03", "Olive Terrace Distribution Point", "نقطة توزيع مدرجات الزيتون", "AR-02", "distribution_point", -1.0, 1.5),
    ("SITE-04", "Cedarbrook Community Hall", "قاعة جدول الأرز", "AR-01", "community_centre", 1.0, 1.0),
    ("SITE-05", "Northfield Distribution Point", "نقطة توزيع الحقل الشمالي", "AR-05", "distribution_point", -2.0, -1.0),
    ("SITE-06", "Almond Grove Distribution Point", "نقطة توزيع بستان اللوز", "AR-09", "distribution_point", 1.0, -2.0),
    ("SITE-07", "Eastgate Health Room", "غرفة صحة البوابة الشرقية", "AR-10", "clinic", -1.5, 1.0),
    ("SITE-08", "Lantern Bay Distribution Point", "نقطة توزيع خليج الفانوس", "AR-12", "distribution_point", 1.0, 2.0),
    ("SITE-09", "Saltmarsh Distribution Point", "نقطة توزيع المستنقع المالح", "AR-07", "distribution_point", 2.5, 1.0),
    ("SITE-10", "Pinecrest Community Hall", "قاعة قمة الصنوبر", "AR-11", "community_centre", -1.0, -1.0),
]

# Areas whose households each distribution point serves.
SITE_CATCHMENT: dict[str, list[str]] = {
    "SITE-01": ["AR-08", "AR-11"],
    "SITE-02": ["AR-04"],
    "SITE-03": ["AR-02", "AR-01", "AR-03"],
    "SITE-05": ["AR-05", "AR-06"],
    "SITE-06": ["AR-09", "AR-10"],
    "SITE-08": ["AR-12"],
    "SITE-09": ["AR-07"],
}

# Warehouse that supplies each distribution site.
SITE_WAREHOUSE: dict[str, str] = {
    "SITE-01": "WH-01",
    "SITE-02": "WH-03",
    "SITE-03": "WH-01",
    "SITE-04": "WH-01",
    "SITE-05": "WH-02",
    "SITE-06": "WH-02",
    "SITE-07": "WH-02",
    "SITE-08": "WH-03",
    "SITE-09": "WH-03",
    "SITE-10": "WH-01",
}

# (warehouse_id, name, area_id, dx, dy)
WAREHOUSES: list[tuple[str, str, str, float, float]] = [
    ("WH-01", "Central Depot", "AR-08", -2.0, 2.0),
    ("WH-02", "North Depot", "AR-05", 2.0, -2.0),
    ("WH-03", "Coastal Depot", "AR-04", 1.0, -2.5),
]

# (item_id, name, category, unit, unit_weight_kg, person_days_per_unit)
ITEMS: list[tuple[str, str, str, str, float, float | None]] = [
    ("ITM-FP-STD", "Standard food parcel", "food_parcel", "parcel", 15.0, 150.0),
    ("ITM-FP-SML", "Small food parcel", "food_parcel", "parcel", 8.0, 60.0),
    ("ITM-RTE", "Ready-to-eat meal", "rte_meal", "meal", 0.5, None),
    ("ITM-HYG", "Family hygiene kit", "hygiene", "kit", 4.0, None),
    ("ITM-BLK", "Thermal blanket", "shelter", "piece", 2.0, None),
    ("ITM-WTR", "Drinking water pack (6 x 1.5 L)", "water", "pack", 9.0, None),
    ("ITM-MAT", "Foam mattress", "shelter", "piece", 6.0, None),
    ("ITM-MED", "First aid kit", "medical", "kit", 1.5, None),
]

# (staff_id, display_name, role)
STAFF: list[tuple[str, str, str]] = [
    ("OPR-01", "Operator 01", "operator"),
    ("OPR-02", "Operator 02", "operator"),
    ("OPR-03", "Operator 03", "operator"),
    ("OPR-04", "Operator 04", "operator"),
    ("CW-01", "Caseworker 01", "caseworker"),
    ("CW-02", "Caseworker 02", "caseworker"),
    ("CW-03", "Caseworker 03", "caseworker"),
    ("CRD-01", "Coordinator 01", "coordinator"),
    ("CRD-02", "Coordinator 02", "coordinator"),
    ("DO-01", "Duty Officer 01", "duty_officer"),
    ("DO-02", "Duty Officer 02", "duty_officer"),
    ("WHO-01", "Warehouse Officer 01", "warehouse_officer"),
    ("WHO-02", "Warehouse Officer 02", "warehouse_officer"),
    ("FW-01", "Field Worker 01", "field_worker"),
    ("FW-02", "Field Worker 02", "field_worker"),
    ("FW-03", "Field Worker 03", "field_worker"),
    ("ADM-01", "Administrator 01", "admin"),
]

# Fictional names: (latin, arabic, [latin spelling variants]). Combinations are random,
# so no generated name refers to a real person.
FIRST_NAMES_M: list[tuple[str, str, list[str]]] = [
    ("Mohammad", "محمد", ["Muhammad", "Mohamad", "Mhamad"]),
    ("Ahmad", "أحمد", ["Ahmed", "Ahmet"]),
    ("Hassan", "حسن", ["Hasan"]),
    ("Youssef", "يوسف", ["Yousef", "Yusuf"]),
    ("Ali", "علي", ["Aly"]),
    ("Omar", "عمر", ["Umar"]),
    ("Elias", "الياس", ["Ilyas", "Elie"]),
    ("Karim", "كريم", ["Kareem"]),
    ("Samir", "سمير", ["Sameer"]),
    ("Fadi", "فادي", ["Fady"]),
    ("Khaled", "خالد", ["Khalid"]),
    ("Walid", "وليد", ["Waleed"]),
    ("Bassam", "بسام", ["Basam"]),
    ("Nabil", "نبيل", ["Nabeel"]),
]
FIRST_NAMES_F: list[tuple[str, str, list[str]]] = [
    ("Fatima", "فاطمة", ["Fatme", "Fatmeh", "Fatma"]),
    ("Maryam", "مريم", ["Mariam", "Miriam"]),
    ("Rania", "رانيا", ["Ranya"]),
    ("Zeinab", "زينب", ["Zaynab", "Zainab"]),
    ("Layla", "ليلى", ["Leila", "Laila"]),
    ("Nour", "نور", ["Noor"]),
    ("Hiba", "هبة", ["Heba"]),
    ("Rima", "ريما", ["Reema"]),
    ("Samira", "سميرة", ["Sameera"]),
    ("Dalia", "داليا", ["Dalya"]),
    ("Amal", "أمل", ["Amel"]),
    ("Joumana", "جمانة", ["Jumana"]),
    ("Salma", "سلمى", ["Selma"]),
]
FAMILY_NAMES: list[tuple[str, str, list[str]]] = [
    ("Khalil", "خليل", ["Khaleel"]),
    ("Mansour", "منصور", ["Mansur"]),
    ("Saleh", "صالح", ["Salih"]),
    ("Darwish", "درويش", ["Darweesh"]),
    ("Najjar", "نجار", ["Al-Najjar", "Najar"]),
    ("Rahal", "رحال", ["Rahhal"]),
    ("Azar", "عازار", ["Azzar"]),
    ("Fakhoury", "فاخوري", ["Fakhouri"]),
    ("Hamdan", "حمدان", ["Hamdane"]),
    ("Issa", "عيسى", ["Eissa"]),
    ("Karam", "كرم", ["Karram"]),
    ("Nasser", "ناصر", ["Nasir"]),
    ("Tannous", "طنوس", ["Tanios"]),
    ("Yassin", "ياسين", ["Yasin"]),
    ("Zein", "زين", ["Zain"]),
    ("Rizk", "رزق", ["Rizq"]),
    ("Sayegh", "صايغ", ["Sayigh"]),
    ("Bitar", "بيطار", ["Baytar"]),
]

# Fictional service directory: (provider_id, name, name_ar, area_id, dx, dy, open_days, languages,
#   [(category, service_name, keywords, weekly_capacity, current_load, eligibility, accepts_new)])
PROVIDERS: list[tuple] = [
    (
        "PRV-01",
        "Riverbend Community Kitchen",
        "مطبخ منعطف النهر المجتمعي",
        "AR-08",
        0.5,
        0.5,
        "Mon-Sat",
        "ar,en",
        [
            ("food", "Hot meals", "food,meal,hungry,cook,طعام,وجبة,جائع", 120, 95, "Any household in Riverbend or Pinecrest.", 1),
            (
                "food",
                "Food parcel registration",
                "food,parcel,rice,flour,حصة,غذائية,طحين",
                60,
                30,
                "Households not registered for parcels elsewhere.",
                1,
            ),
        ],
    ),
    (
        "PRV-02",
        "Harbor Flats Health Point",
        "نقطة صحة سهول المرفأ",
        "AR-04",
        -1.0,
        0.5,
        "Mon-Fri",
        "ar,en",
        [
            (
                "health",
                "General clinic",
                "fever,doctor,clinic,sick,cough,حرارة,طبيب,مستوصف,مريض",
                80,
                70,
                "Free for all residents; children prioritised.",
                1,
            ),
            (
                "health",
                "Chronic medication",
                "insulin,diabetes,medication,prescription,heart,أنسولين,سكري,دواء,وصفة",
                40,
                39,
                "Needs an existing diagnosis.",
                1,
            ),
        ],
    ),
    (
        "PRV-03",
        "Northfield Shelter Collective",
        "تجمع إيواء الحقل الشمالي",
        "AR-05",
        1.0,
        1.0,
        "Daily",
        "ar",
        [
            (
                "shelter",
                "Collective shelter beds",
                "displaced,evicted,shelter,sleeping,نزح,إخلاء,إيواء,ينام",
                30,
                26,
                "Displaced households; families with children first.",
                1,
            ),
            (
                "shelter",
                "Shelter repair kits",
                "roof,window,door,repair,leak,سقف,نوافذ,باب,ترميم,يدلف",
                25,
                10,
                "Owner or tenant with landlord consent.",
                1,
            ),
        ],
    ),
    (
        "PRV-04",
        "Stonebridge Legal Aid Desk",
        "مكتب المساعدة القانونية في الجسر الحجري",
        "AR-03",
        0.0,
        -1.0,
        "Tue,Thu",
        "ar,en",
        [
            (
                "protection",
                "Documentation support",
                "documents,identity,papers,checkpoint,أوراق,ثبوتية,حواجز",
                20,
                8,
                "Anyone who lost civil documents.",
                1,
            ),
            ("protection", "Eviction advice", "eviction,landlord,rent,leave,إخلاء,صاحب البيت,إيجار", 15, 14, "Tenants facing eviction.", 1),
        ],
    ),
    (
        "PRV-05",
        "Juniper Hill Women's Space",
        "مساحة النساء في تلة العرعر",
        "AR-06",
        -1.0,
        0.0,
        "Mon-Fri",
        "ar,en",
        [
            (
                "protection",
                "Safe space and case management",
                "safety,afraid,harassment,unsafe,threat,سلامة,خائفة,تحرش,تهديد",
                25,
                12,
                "Women and girls; confidential.",
                1,
            ),
            ("health", "Maternal health sessions", "pregnant,maternal,baby,حامل,حمل,طفل", 20, 9, "Pregnant and breastfeeding women.", 1),
        ],
    ),
    (
        "PRV-06",
        "Almond Grove Water Committee",
        "لجنة مياه بستان اللوز",
        "AR-09",
        0.5,
        -0.5,
        "Mon-Sat",
        "ar",
        [
            (
                "water_hygiene",
                "Water trucking",
                "water,tank,drinking,truck,مياه,خزان,شرب,صهريج",
                50,
                44,
                "Buildings without mains water.",
                1,
            ),
            (
                "water_hygiene",
                "Hygiene kit distribution",
                "hygiene,soap,diapers,sanitary,نظافة,صابون,حفاضات,فوط",
                70,
                30,
                "Registered households.",
                1,
            ),
        ],
    ),
    (
        "PRV-07",
        "Lantern Bay Cash Desk",
        "مكتب المساعدات النقدية في خليج الفانوس",
        "AR-12",
        0.0,
        0.5,
        "Mon-Thu",
        "ar,en",
        [
            (
                "cash",
                "Emergency cash grant",
                "cash,rent,money,bills,debt,income,نقدية,إيجار,مال,فواتير,دين,دخل",
                40,
                38,
                "One grant per household per quarter.",
                1,
            ),
        ],
    ),
    (
        "PRV-08",
        "Eastgate Family Support Centre",
        "مركز دعم الأسرة في البوابة الشرقية",
        "AR-10",
        1.0,
        0.0,
        "Mon-Fri",
        "ar,en",
        [
            (
                "protection",
                "Child protection follow-up",
                "child,school,working,street,missing,separated,طفل,مدرسة,يعمل,مفقود",
                20,
                11,
                "Children under 18 and their caregivers.",
                1,
            ),
            ("cash", "School fees support", "school,fees,transport,أقساط,مدرسة,مواصلات", 30, 30, "Children enrolled this year.", 0),
        ],
    ),
    (
        "PRV-09",
        "Saltmarsh Mobile Clinic",
        "العيادة المتنقلة في المستنقع المالح",
        "AR-07",
        0.0,
        0.0,
        "Wed,Sat",
        "ar",
        [
            (
                "health",
                "Mobile clinic visits",
                "doctor,wound,injured,elderly,fever,طبيب,جرح,إصابة,مسن,حرارة",
                35,
                20,
                "Residents of Saltmarsh and Lantern Bay.",
                1,
            ),
        ],
    ),
    (
        "PRV-10",
        "Cedarbrook Neighbourhood Pantry",
        "مخزن الجيرة في جدول الأرز",
        "AR-01",
        -0.5,
        0.5,
        "Mon,Wed,Fri",
        "ar,en",
        [
            (
                "food",
                "Weekly pantry basket",
                "food,groceries,bread,canned,elderly,طعام,خبز,معلبات,مسن",
                50,
                22,
                "Households in the Highland District.",
                1,
            ),
            ("food", "Baby food and formula", "baby,formula,milk,حليب,أطفال", 20, 15, "Children under two.", 1),
        ],
    ),
    (
        "PRV-11",
        "Pinecrest Repair Volunteers",
        "متطوعو الترميم في قمة الصنوبر",
        "AR-11",
        0.5,
        0.0,
        "Sat,Sun",
        "ar",
        [
            (
                "shelter",
                "Minor home repairs",
                "repair,roof,window,door,cold,ترميم,سقف,نوافذ,باب,برد",
                12,
                4,
                "Elderly, disabled or female-headed households first.",
                1,
            ),
            (
                "shelter",
                "Blankets and mattresses",
                "blankets,mattress,floor,cold,بطانيات,فرش,برد",
                40,
                18,
                "Any household with damaged shelter.",
                1,
            ),
        ],
    ),
    (
        "PRV-12",
        "Olive Terrace Health and Hygiene Room",
        "غرفة الصحة والنظافة في مدرجات الزيتون",
        "AR-02",
        0.0,
        1.0,
        "Mon-Fri",
        "ar,en",
        [
            (
                "water_hygiene",
                "Sanitation repairs",
                "toilet,sewage,blocked,leak,مرحاض,صرف,مسدود",
                10,
                3,
                "Shared buildings and shelters.",
                1,
            ),
            ("health", "Nurse consultations", "nurse,fever,cough,baby,ممرضة,حرارة,سعال", 40, 25, "All residents.", 1),
        ],
    ),
]

COMPLAINT_TOPICS: dict[str, dict[str, list[str]]] = {
    "waiting_time": {
        "en": [
            "Waited more than four hours at the distribution point.",
            "The queue at the site was very long and there was no shade.",
            "Was told to come back the next day after waiting all morning.",
        ],
        "ar": [
            "انتظرنا أكثر من أربع ساعات في نقطة التوزيع.",
            "الطابور كان طويلا جدا ولا يوجد ظل.",
            "قالوا لنا أن نعود في اليوم التالي بعد انتظار طوال الصباح.",
        ],
    },
    "parcel_contents": {
        "en": [
            "The parcel was missing the oil and sugar.",
            "Some cans in the parcel were dented and past their date.",
            "Parcel had less rice than last month.",
        ],
        "ar": [
            "الحصة كانت ناقصة الزيت والسكر.",
            "بعض المعلبات في الحصة كانت منبعجة ومنتهية الصلاحية.",
            "الحصة فيها أرز أقل من الشهر الماضي.",
        ],
    },
    "exclusion": {
        "en": [
            "Family was removed from the list without explanation.",
            "Neighbours received parcels but they did not.",
            "Was told the household is not eligible but nobody explained why.",
        ],
        "ar": ["تم حذف العائلة من اللائحة دون تفسير.", "الجيران استلموا حصصا ونحن لا.", "قالوا إن العائلة غير مؤهلة ولم يشرح أحد السبب."],
    },
    "staff_conduct": {
        "en": ["A volunteer at the site spoke rudely to the caller.", "Felt disrespected during registration."],
        "ar": ["أحد المتطوعين في الموقع تكلم بطريقة غير لائقة.", "شعرت بعدم الاحترام أثناء التسجيل."],
    },
    "information": {
        "en": ["Nobody told them the distribution date had changed.", "Hotline gave different answers on two calls."],
        "ar": ["لم يخبرهم أحد بتغيير موعد التوزيع.", "الخط الساخن أعطى أجوبة مختلفة في مكالمتين."],
    },
    "callback_not_received": {
        "en": ["Was promised a callback last week and nobody called.", "Still waiting for the caseworker to call back."],
        "ar": ["وعدونا باتصال الأسبوع الماضي ولم يتصل أحد.", "ما زلنا ننتظر اتصال المرشدة الاجتماعية."],
    },
}

REPEAT_CALL_NOTES: dict[str, list[str]] = {
    "en": [
        "Calling again about the same complaint, no update yet.",
        "Repeat call: asks what happened with the complaint.",
        "Called back to add that the problem happened again.",
    ],
    "ar": ["يتصل مرة أخرى بخصوص نفس الشكوى، لا جديد.", "اتصال متكرر: يسأل ماذا حصل بالشكوى.", "اتصل ليضيف أن المشكلة تكررت."],
}

INFO_CALL_NOTES: dict[str, list[str]] = {
    "en": [
        "Asked about the next distribution date in {area}.",
        "Asked which documents are needed to register.",
        "Asked for the opening hours of the {area} distribution point.",
        "Asked whether the hotline also covers {area}.",
        "Asked how to update the household size on file.",
    ],
    "ar": [
        "سأل عن موعد التوزيع القادم في {area}.",
        "سأل عن الأوراق المطلوبة للتسجيل.",
        "سأل عن ساعات عمل نقطة التوزيع في {area}.",
        "سأل إن كان الخط الساخن يغطي {area}.",
        "سأل كيف يحدّث عدد أفراد الأسرة في الملف.",
    ],
}

FOLLOW_UP_NOTES: dict[str, list[str]] = {
    "en": ["Following up on the earlier request, asks for an update.", "Calling to check whether the request was approved."],
    "ar": ["يتابع الطلب السابق ويسأل عن أي جديد.", "يتصل ليتأكد إن تمت الموافقة على الطلب."],
}

INCIDENT_TYPES: dict[str, list[str]] = {
    "road_closure": [
        "Main road blocked by debris after heavy rain.",
        "Road closed by local committee for several hours.",
        "Bridge approach closed for inspection.",
    ],
    "armed_clash": ["Exchange of fire reported near the market.", "Armed confrontation reported on the ring road."],
    "unrest": ["Large gathering and burning tyres at the junction.", "Protest blocking access to the municipal building."],
    "shelling": ["Impacts reported on the outskirts overnight.", "Shelling reported near agricultural land."],
    "checkpoint_delay": ["Long delays at the checkpoint on the coastal road.", "Vehicles searched at a new checkpoint, two-hour delay."],
    "crime": ["Theft of supplies reported from a parked vehicle.", "Robbery reported on the evening road."],
}

# Situation report templates: (title, [(heading, text)]) with {area}, {area2}, {n}, {m}, {k}
SITREP_TEMPLATES: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "Flash flooding in {area}",
        [
            (
                "Situation",
                "Heavy rainfall over two days caused flash flooding in low-lying streets of {area}. About {n} households reported water entering their homes, and the main road to {area2} was cut for most of a day.",
            ),
            (
                "Needs",
                "Families asked mainly for mattresses, blankets and hygiene kits. Drinking water was contaminated in several buildings, and the water committee requested additional trucking.",
            ),
            (
                "Response",
                "Teams distributed {m} hygiene kits and {k} blankets from the Coastal Depot. A mobile clinic visit was added for skin infections and diarrhoea cases.",
            ),
            ("Gaps", "Pumping equipment was insufficient. Several ground-floor households remain displaced with relatives."),
        ],
    ),
    (
        "Cold wave response in {area}",
        [
            (
                "Situation",
                "Night temperatures fell below freezing for five nights in {area}. Elderly people and families in unfinished buildings were most exposed.",
            ),
            (
                "Needs",
                "Requests to the hotline for blankets, heating fuel and shelter repair doubled compared with the previous week, reaching {n} calls.",
            ),
            (
                "Response",
                "Volunteers repaired windows and doors in {m} homes and distributed {k} thermal blankets. Two collective shelters extended opening hours.",
            ),
            ("Gaps", "Heating fuel is not covered by current stock. Coordination with the shelter collective continues."),
        ],
    ),
    (
        "Displacement from {area} to {area2}",
        [
            (
                "Situation",
                "Insecurity on the ring road led about {n} households to move from {area} to relatives and collective shelters in {area2}.",
            ),
            (
                "Needs",
                "New arrivals need food parcels, registration, and documentation support because several families left identity papers behind.",
            ),
            (
                "Response",
                "Registration desks recorded {m} new households in three days. The legal aid desk held extra sessions on lost documents.",
            ),
            (
                "Gaps",
                "Collective shelter capacity in {area2} is close to full. Protection staff flagged overcrowding and privacy concerns for women and girls.",
            ),
        ],
    ),
    (
        "Water shortage in {area}",
        [
            ("Situation", "A damaged pumping station left {area} without mains water for nine days."),
            ("Needs", "Households reported no drinking water and blocked toilets. Hotline calls about water rose to {n}."),
            ("Response", "Water trucking reached {m} buildings and {k} water packs were distributed to elderly and disabled residents."),
            ("Gaps", "Trucking costs are high and the repair timeline is unclear. Diarrhoea cases among children are being monitored."),
        ],
    ),
    (
        "Food distribution round in {area}",
        [
            ("Situation", "The monthly food distribution round in {area} reached {n} households over two days."),
            (
                "Needs",
                "Larger households said one standard parcel does not last the full month. Several complaints concerned missing oil in parcels.",
            ),
            (
                "Response",
                "A second parcel was given to households of six or more. Ready-to-eat meals were provided to {m} newly displaced families.",
            ),
            ("Gaps", "Waiting times exceeded three hours on the first day. Site layout and queue management will be reviewed."),
        ],
    ),
    (
        "Disease alert in {area}",
        [
            (
                "Situation",
                "The health room in {area} reported a rise in fever and cough among children, with {n} consultations in one week.",
            ),
            ("Needs", "Families requested medicines, paediatric consultations and hygiene supplies."),
            (
                "Response",
                "Nurse consultations were extended and {m} hygiene kits distributed. Messages on handwashing were shared through community volunteers.",
            ),
            ("Gaps", "Chronic medication stocks are low. Referral transport to the general clinic is limited."),
        ],
    ),
    (
        "Road access restrictions affecting {area}",
        [
            ("Situation", "Repeated road closures between {area} and {area2} delayed convoys by up to five hours."),
            ("Needs", "Distribution in {area} was postponed twice, and {n} households are now due for food support."),
            (
                "Response",
                "The convoy plan was rerouted through {area2}. Duty officers raised the movement advisory to restricted for two days.",
            ),
            ("Gaps", "Fuel consumption increased with longer routes. Vehicle maintenance is falling behind schedule."),
        ],
    ),
    (
        "Cash assistance round in {area}",
        [
            ("Situation", "The quarterly emergency cash round covered {n} households in {area}."),
            ("Needs", "Most recipients said they would use the cash for rent and debts at local shops."),
            ("Response", "Cash desks processed {m} grants. Hotline operators answered questions about eligibility and card top-ups."),
            ("Gaps", "{k} households were found eligible but could not be served because the round reached its ceiling."),
        ],
    ),
]
