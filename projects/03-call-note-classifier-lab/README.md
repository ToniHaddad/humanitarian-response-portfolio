# 03 · Call note classifier lab

## Problem
Project 01 suggests a request category for each call note. Before anyone trusts that suggestion, reviewers need to see how well the model works, where it is wrong, and what it does when it is unsure.

## Intended user
Reviewers and data staff.

## Technique
Classification. The shared model (`shared/classifier.py`) is a character n-gram TF-IDF feeding a logistic regression, trained locally on the synthetic Arabic and English notes in `classifier_notes`. This project does not copy the model; it evaluates it. Project 01 uses the same model, so these numbers are the evidence for 01.

## Inputs
Table `classifier_notes` (600 labelled synthetic notes: 383 train, 217 test) and the setting `classifier_confidence_threshold` (0.6).

## Assumptions
- The notes are synthetic and templated. The test split holds one note template per category and language that never appears in training, plus every fourth other note, so the test also measures new phrasing.
- A prediction is shown when its confidence is at or above the threshold; below it the category is left blank for the operator (`shared.classifier.predict`).
- The app trains once per session from the current database, so the shown model always matches the data.
- **No consequential action exists here.** The app saves nothing. The operator's category box in "Try a note" is a demo only.

## Limitations
- Because the notes are templated, real call notes would score lower than the figures here. The held-out phrasing test gives 76.5% (exact figure on a fresh seed: 166 of 217 = 0.765); realistic accuracy is lower still.
- Health is the weakest category (44.7%): its test notes are often predicted as Food (13) or Shelter (6).
- At threshold 0.6, 43.8% of test notes (95 of 217) are left blank; the 122 shown are all correct (100%). That is a property of this seed, not a guarantee.
- Some notes mention two needs and are labelled with the first, so a few mistakes are partly the labels' fault.

## Launch
`python run.py 3`, or `streamlit run projects/03-call-note-classifier-lab/app.py --server.port 8503`. Port 8503.

## Screenshot
![Call note classifier lab](screenshot.png)

## Try this demo
1. Open the app. Expect 383 training notes, 217 test notes, overall accuracy 76.5%, 43.8% left blank at threshold 0.60, accuracy when shown 100.0%.
2. In "Accuracy per category" the weakest bar is Health (44.7%). In the confusion matrix its row shows 17 correct, 13 predicted as Food, 6 as Shelter, 2 as Cash assistance.
3. In "Try a note" type `Family of 6 has no food left, asking for a food parcel and rice.` Expect "Predicted category: Food (confidence 91%)" and the probability per category.
4. Type `Caller asked a general question about the office.` Expect "Left blank for the operator: confidence 28% below threshold 60%", the top guess (Food) as a hint only, and a selectbox where you can fill in a category (not saved).
5. The sample buttons fill an English and an Arabic note; the Arabic water note is predicted Water and hygiene.

## Tests
`tests/test_p03_classifier_lab.py`: app starts; confusion matrix sums to the test count; diagonal over total equals accuracy (0.765); per-category counts match; misclassified list equals the off-diagonal count; low-confidence predictions are left blank below, at and above the threshold (tiny threshold and 0.99); empty text is blank; threshold read from settings; the "Try a note" walkthrough in AppTest (clear note gives Food, vague note left blank).

Libraries: scikit-learn, plotly, pandas (already in the shared requirements).
