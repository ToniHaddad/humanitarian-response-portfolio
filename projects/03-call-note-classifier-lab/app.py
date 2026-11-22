import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in (str(ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import p03_classifier_lab as p03  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from shared import charts, classifier, db, ui  # noqa: E402
from shared.call_notes import CATEGORY_LABELS  # noqa: E402

ui.page_header(project_number=3)

SAMPLES = {
    "Sample note (English)": "Family of 6 has no food left, asking for a food parcel and rice.",
    "Sample note (Arabic)": "العائلة تحتاج إلى مياه للشرب وصابون، لا يوجد ماء نظيف في المنزل.",
}


@st.cache_resource(show_spinner="Training the model on the training notes...")
def get_model():  # trained once per session from the current database
    with db.transaction() as conn:
        return classifier.load_or_train(conn, retrain=True)


def label(category: str | None) -> str:
    return CATEGORY_LABELS.get(category or "", category or "")


model = get_model()
with db.transaction() as conn:
    threshold = classifier.threshold_from_settings(conn)
    n_train = p03.count_notes(conn, "train")
    ev = p03.evaluate(conn, model, threshold)

st.info(
    "Nothing is saved here. The lab trains a small model on synthetic notes and measures it on notes it never saw. "
    "The category box in 'Try a note' is a demo only."
)

ui.metric_row(
    [
        ("Training notes (count)", n_train),
        ("Test notes (count)", ev.n_test),
        ("Accuracy, test notes (%)", f"{ev.accuracy:.1%}", "Share of test notes where the top guess equals the true category."),
        (
            "Left blank (% of notes)",
            f"{ev.blank_share:.1%}",
            f"{ev.blank_count} of {ev.n_test} test notes have confidence below the threshold ({threshold:.2f}) and are left for the operator.",
        ),
        (
            "Accuracy if shown (%)",
            "n/a" if ev.shown_accuracy is None else f"{ev.shown_accuracy:.1%}",
            f"Accuracy on the {ev.shown_count} test notes whose confidence reaches the threshold.",
        ),
    ]
)

# -- per category ----------------------------------------------------------
ui.section("Accuracy per category", "Recall: of the test notes truly in a category, the share the model put there.")
cat_df = pd.DataFrame(ev.per_category)
cat_df["Category"] = cat_df["category"].map(label)
cat_df["Accuracy (%)"] = (cat_df["accuracy"] * 100).round(1)
left, right = st.columns([3, 2])
with left:
    fig = charts.bar(cat_df, "Category", "Accuracy (%)", "Category", "Accuracy (%)")
    fig.update_yaxes(range=[0, 100])
    st.plotly_chart(fig, width="stretch")
with right:
    ui.table(
        cat_df.rename(columns={"notes": "Test notes (count)", "correct": "Correct (count)"})[
            ["Category", "Test notes (count)", "Correct (count)", "Accuracy (%)"]
        ]
    )
weak = p03.weakest_category(ev)
st.caption(f"Weakest category: {label(weak['category'])} at {weak['accuracy']:.1%}. See its row in the confusion matrix below.")
st.caption(
    "By language: "
    + ", ".join(f"{'Arabic' if r['language'] == 'ar' else 'English'} {r['accuracy']:.1%} ({r['notes']} notes)" for r in ev.per_language)
)

# -- confusion matrix -------------------------------------------------------
ui.section("Confusion matrix", "Rows are the true category, columns what the model predicted. The diagonal is correct.")
names = [label(c) for c in ev.labels]
st.plotly_chart(
    charts.heatmap(ev.matrix, names, names, "Predicted category", "True category", "Notes (count)"),
    width="stretch",
)

# -- misclassified ----------------------------------------------------------
ui.section("Misclassified test notes", f"{len(ev.misclassified)} notes, most confident mistakes first. Arabic notes read right to left.")
if ev.misclassified:
    rows = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{:.0%}</td></tr>".format(
            ui.bidi_html(m["text"]), m["language"], label(m["true"]), label(m["predicted"]), m["confidence"]
        )
        for m in ev.misclassified
    )
    st.markdown(
        '<div style="max-height:420px;overflow:auto"><table style="width:100%"><thead><tr>'
        "<th>Note</th><th>Language</th><th>True category</th><th>Predicted</th><th>Confidence (%)</th>"
        f"</tr></thead><tbody>{rows}</tbody></table></div>",
        unsafe_allow_html=True,
    )
else:
    st.success("No misclassified test notes.")

# -- try a note --------------------------------------------------------------
ui.section("Try a note", f"Type a call note in Arabic or English. Predictions below confidence {threshold:.2f} are left blank.")


def _fill(text: str) -> None:
    st.session_state["p03_note"] = text


cols = st.columns(len(SAMPLES))
for col, (name, text) in zip(cols, SAMPLES.items(), strict=True):
    col.button(name, key=f"p03_{name}", on_click=_fill, args=(text,))
note = st.text_area("Call note", key="p03_note", height=100)

if note.strip():
    res = p03.try_note(model, note, threshold)
    if res["blank"]:
        st.warning(f"Left blank for the operator: confidence {res['confidence']:.0%} below threshold {threshold:.0%}.")
        st.caption(f"Hint only, not a prediction: the model's top guess is {label(res['top_category'])} ({res['confidence']:.0%}).")
        choice = st.selectbox(
            "Operator fills in the category (demo only, not saved)",
            ["(blank)", *[label(c) for c in ev.labels]],
            key="p03_operator",
        )
        if choice != "(blank)":
            st.success(f"Operator chose {choice}. Nothing was saved.")
    else:
        st.success(f"Predicted category: {label(res['category'])} (confidence {res['confidence']:.0%})")
    prob_df = pd.DataFrame(
        {
            "Category": [label(c) for c in res["probabilities"]],
            "Probability (%)": [round(p * 100, 1) for p in res["probabilities"].values()],
        }
    ).sort_values("Probability (%)", ascending=False)
    ui.table(prob_df)
