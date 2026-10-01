(() => {
    const preferredMimeTypes = [
        "audio/webm;codecs=opus",
        "audio/webm",
        "audio/ogg;codecs=opus",
        "audio/mp4",
    ];
    const MAX_AUDIO_BYTES = 15 * 1024 * 1024;
    const MAX_ANSWER_MS = 90000;

    const normalize = (value) =>
        value
            .normalize("NFKD")
            .replace(/[\u0300-\u036f]/g, "")
            .toLocaleLowerCase()
            .replace(/[^a-z0-9]+/g, " ")
            .trim();

    function speakAmericanEnglish(text, { onend, onerror } = {}) {
        const maleEnglishVoices = window.speechSynthesis
            .getVoices()
            .filter((voice) => {
                const name = voice.name.toLocaleLowerCase();
                return (
                    /^en(?:-|$)/i.test(voice.lang) &&
                    (/\bmale\b|\bmasculine\b/.test(name) ||
                        /\b(mark|david|guy|christopher|eric|brian|james|daniel|alex|tom|ryan|andrew|thomas|william|matthew|liam|roger|tony|brandon)\b/.test(name))
                );
            });
        maleEnglishVoices.sort((left, right) => {
            const score = (voice) =>
                (/^en-us(?:\b|-)/i.test(voice.lang) ? 4 : 0) +
                (/neural|natural|premium|enhanced/i.test(voice.name) ? 2 : 0) +
                (voice.default ? 1 : 0);
            return score(right) - score(left);
        });
        const voice = maleEnglishVoices[0];
        if (!voice) {
            return false;
        }

        const utterance = new SpeechSynthesisUtterance(text);
        utterance.voice = voice;
        utterance.lang = voice.lang;
        utterance.rate = 0.92;
        utterance.onend = onend || null;
        utterance.onerror = onerror || null;
        window.speechSynthesis.speak(utterance);
        return true;
    }

    const missingEnglishVoiceMessage =
        "No male English voice is available in this browser. Add a male English (United States) speech voice in your device settings, then reload this page. You can still read the question and start recording manually.";

    function fieldLabel(field, wrapper) {
        const label = wrapper.querySelector("label");
        return (label?.textContent || field.name || "This field")
            .replace(/\*/g, "")
            .replace(/\s+/g, " ")
            .trim();
    }

    function fieldSection(wrapper) {
        const section = wrapper.closest(
            ".section, .complaint-card, .signal-card",
        );
        const heading = section?.querySelector(
            ".section-title h3, .section-heading h2",
        );
        return heading?.textContent.trim() || "";
    }

    function getFields(form) {
        return Array.from(
            form.querySelectorAll("input, select, textarea"),
        ).filter((field) => {
            if (
                field.disabled ||
                field.readOnly ||
                field.type === "hidden" ||
                field.type === "submit" ||
                field.type === "button" ||
                field.type === "reset" ||
                !field.name
            ) {
                return false;
            }
            return true;
        }).map((field) => {
            const wrapper = field.closest(".field") || field.parentElement;
            const label = fieldLabel(field, wrapper);
            const required =
                field.required ||
                Boolean(wrapper.querySelector(".required")) ||
                Boolean(field.closest(".field")?.querySelector(".required"));
            const options =
                field instanceof HTMLSelectElement
                    ? Array.from(field.options)
                        .filter((option) => option.value !== "")
                        .map((option) => option.textContent.trim())
                    : [];
            return {
                field,
                wrapper,
                label,
                required,
                options,
                section: fieldSection(wrapper),
            };
        });
    }

    function asDateValue(text) {
        const normalized = text.trim();
        let match = normalized.match(
            /^(\d{4})[-/\s](\d{1,2})[-/\s](\d{1,2})$/,
        );
        if (match) {
            const [, year, month, day] = match;
            return validDate(year, month, day);
        }

        const monthNames =
            "january february march april may june july august september october november december".split(
                " ",
            );
        const monthPattern = monthNames.join("|");
        match = normalized.match(
            new RegExp(`^(\\d{1,2})(?:st|nd|rd|th)?\\s+(${monthPattern})\\w*[,]?\\s+(\\d{4})$`, "i"),
        );
        if (match) {
            const month = monthNames.findIndex((name) =>
                name.startsWith(match[2].toLowerCase()),
            ) + 1;
            return validDate(match[3], month, match[1]);
        }

        match = normalized.match(
            new RegExp(`^(${monthPattern})\\w*\\s+(\\d{1,2})[,]?\\s+(\\d{4})$`, "i"),
        );
        if (match) {
            const month = monthNames.findIndex((name) =>
                name.startsWith(match[1].toLowerCase()),
            ) + 1;
            return validDate(match[3], month, match[2]);
        }
        return null;
    }

    function validDate(yearValue, monthValue, dayValue) {
        const year = Number(yearValue);
        const month = Number(monthValue);
        const day = Number(dayValue);
        const candidate = new Date(Date.UTC(year, month - 1, day));
        if (
            !Number.isInteger(year) ||
            candidate.getUTCFullYear() !== year ||
            candidate.getUTCMonth() !== month - 1 ||
            candidate.getUTCDate() !== day
        ) {
            return null;
        }
        return `${String(year).padStart(4, "0")}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
    }

    function asTimeValue(text) {
        const normalized = text.trim().toLocaleLowerCase();
        const match = normalized.match(
            /\b(\d{1,2})(?:(?::|\s)(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?\b/i,
        );
        if (!match || (!match[2] && !match[3])) {
            return null;
        }

        let hours = Number(match[1]);
        const minutes = Number(match[2] || 0);
        const meridiem = match[3]?.replace(/\./g, "");
        if (
            hours > 23 ||
            minutes > 59 ||
            (meridiem && (hours < 1 || hours > 12))
        ) {
            return null;
        }
        if (meridiem === "pm" && hours < 12) {
            hours += 12;
        } else if (meridiem === "am" && hours === 12) {
            hours = 0;
        }
        return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`;
    }

    function asNumberValue(text) {
        const normalized = text.trim().toLocaleLowerCase().replace(/,/g, "");
        if (/^-?(?:\d+\.?\d*|\.\d+)$/.test(normalized)) {
            return normalized;
        }

        const smallNumbers = {
            zero: 0, one: 1, two: 2, three: 3, four: 4, five: 5,
            six: 6, seven: 7, eight: 8, nine: 9, ten: 10,
            eleven: 11, twelve: 12, thirteen: 13, fourteen: 14,
            fifteen: 15, sixteen: 16, seventeen: 17, eighteen: 18,
            nineteen: 19,
        };
        const tens = {
            twenty: 20, thirty: 30, forty: 40, fifty: 50,
            sixty: 60, seventy: 70, eighty: 80, ninety: 90,
        };
        const words = normalized.replace(/-/g, " ").split(/\s+/);
        if (words.includes("point")) {
            const pointIndex = words.indexOf("point");
            if (
                pointIndex !== 1 ||
                !Object.hasOwn(smallNumbers, words[0]) ||
                words.slice(pointIndex + 1).some((word) => !Object.hasOwn(smallNumbers, word))
            ) {
                return null;
            }
            return `${smallNumbers[words[0]]}.${words.slice(pointIndex + 1).map((word) => smallNumbers[word]).join("")}`;
        }
        if (words[0] === "minus" || words[0] === "negative") {
            words.shift();
            if (!words.length) {
                return null;
            }
        }
        if (
            !words.length ||
            words.some((word) =>
                !Object.hasOwn(smallNumbers, word) &&
                !Object.hasOwn(tens, word) &&
                !["and", "hundred", "thousand"].includes(word),
            )
        ) {
            return null;
        }

        let total = 0;
        let current = 0;
        words.forEach((word) => {
            if (word === "and") {
                return;
            }
            if (word === "hundred") {
                current = Math.max(current, 1) * 100;
            } else if (word === "thousand") {
                total += Math.max(current, 1) * 1000;
                current = 0;
            } else {
                current += smallNumbers[word] ?? tens[word];
            }
        });
        const result = total + current;
        return String(normalized.startsWith("minus ") || normalized.startsWith("negative ") ? -result : result);
    }

    function selectOption(field, answer) {
        const answerText = normalize(answer);
        if (!answerText) {
            return null;
        }
        const options = Array.from(field.options).filter(
            (option) => option.value !== "",
        );
        const exact = options.find(
            (option) => normalize(option.textContent) === answerText,
        );
        if (exact) {
            return exact.value;
        }
        const matches = options.filter((option) => {
            const optionText = normalize(option.textContent);
            return (
                optionText.includes(answerText) ||
                answerText.includes(optionText)
            );
        });
        return matches.length === 1 ? matches[0].value : null;
    }

    function setAnswer(item, answer) {
        const { field } = item;
        const normalized = normalize(answer);

        if (field instanceof HTMLSelectElement) {
            const optionValue = selectOption(field, answer);
            if (optionValue === null) {
                return "Please say one of the displayed choices, or select it manually.";
            }
            field.value = optionValue;
        } else if (field.type === "checkbox") {
            if (
                ["yes", "true", "checked", "tick", "selected"].includes(normalized) ||
                /^(yes|true|checked|tick|selected)\b/.test(normalized)
            ) {
                field.checked = true;
            } else if (
                ["no", "false", "unchecked", "not checked", "not selected"].includes(
                    normalized,
                ) ||
                /^(no|false|unchecked|not checked|not selected)\b/.test(normalized)
            ) {
                field.checked = false;
            } else {
                return "Please answer yes or no.";
            }
        } else if (field.type === "date") {
            const dateValue = asDateValue(answer);
            if (!dateValue) {
                return "Please state the date as YYYY-MM-DD, or say it like “28 September 2026”.";
            }
            field.value = dateValue;
        } else if (field.type === "time") {
            const timeValue = asTimeValue(answer);
            if (!timeValue) {
                return "Please state the time as HH:MM, for example 14:30 or 2:30 PM.";
            }
            field.value = timeValue;
        } else if (field.type === "number") {
            const numberValue = asNumberValue(answer);
            if (numberValue === null || !Number.isFinite(Number(numberValue))) {
                return "I could not identify a number. Please answer again.";
            }
            field.value = numberValue;
        } else {
            field.value = answer.trim();
        }

        field.dispatchEvent(new Event("input", { bubbles: true }));
        field.dispatchEvent(new Event("change", { bubbles: true }));
        return "";
    }

    function setupGuidedVoice(form) {
        const fields = getFields(form);
        if (!fields.length) {
            return;
        }

        const launch = document.createElement("button");
        launch.type = "button";
        launch.className = "guided-voice-launch";
        launch.textContent = "Start guided voice entry";
        launch.setAttribute("aria-haspopup", "dialog");

        const intro = document.createElement("div");
        intro.className = "guided-voice-intro";
        const explanation = document.createElement("p");
        explanation.className = "guided-voice-explanation";
        explanation.textContent =
            "The assistant will ask each form question aloud, one at a time. Your answer is transcribed locally and shown for confirmation before it is entered. You can skip optional fields. The form is not saved until you press its Save button.";
        intro.append(launch, explanation);
        form.insertBefore(intro, form.firstElementChild);

        const dialog = document.createElement("section");
        dialog.className = "guided-voice-dialog";
        dialog.setAttribute("role", "dialog");
        dialog.setAttribute("aria-modal", "true");
        dialog.setAttribute("aria-labelledby", "guided-voice-title");
        dialog.hidden = true;

        const title = document.createElement("h2");
        title.id = "guided-voice-title";
        title.textContent = "Guided voice entry";

        const progress = document.createElement("p");
        progress.className = "guided-voice-progress";
        progress.setAttribute("aria-live", "polite");

        const question = document.createElement("p");
        question.className = "guided-voice-question";
        question.setAttribute("aria-live", "polite");

        const choices = document.createElement("p");
        choices.className = "guided-voice-choices";

        const transcriptLabel = document.createElement("label");
        transcriptLabel.className = "guided-voice-transcript-label";
        transcriptLabel.textContent = "Check or correct your answer";
        const transcript = document.createElement("textarea");
        transcript.className = "guided-voice-transcript";
        transcript.rows = 3;
        transcriptLabel.append(transcript);

        const status = document.createElement("p");
        status.className = "guided-voice-status";
        status.setAttribute("role", "status");
        status.setAttribute("aria-live", "polite");

        const controls = document.createElement("div");
        controls.className = "guided-voice-controls";

        const recordButton = document.createElement("button");
        recordButton.type = "button";
        recordButton.className = "guided-voice-primary";
        recordButton.textContent = "Start answer";

        const confirmButton = document.createElement("button");
        confirmButton.type = "button";
        confirmButton.className = "guided-voice-primary";
        confirmButton.textContent = "Confirm and continue";
        confirmButton.hidden = true;

        const skipButton = document.createElement("button");
        skipButton.type = "button";
        skipButton.className = "guided-voice-secondary";
        skipButton.textContent = "Skip optional field";

        const repeatButton = document.createElement("button");
        repeatButton.type = "button";
        repeatButton.className = "guided-voice-secondary";
        repeatButton.textContent = "Repeat question";

        const closeButton = document.createElement("button");
        closeButton.type = "button";
        closeButton.className = "guided-voice-secondary";
        closeButton.textContent = "Pause and close";

        controls.append(
            recordButton,
            confirmButton,
            skipButton,
            repeatButton,
            closeButton,
        );
        const panel = document.createElement("div");
        panel.className = "guided-voice-panel";
        panel.append(
            title,
            progress,
            question,
            choices,
            transcriptLabel,
            status,
            controls,
        );
        dialog.append(panel);
        document.body.append(dialog);

        let currentIndex = 0;
        let stream = null;
        let recorder = null;
        let chunks = [];
        let recordTimer = null;
        let isRecording = false;
        let discardRecording = false;

        const stopTracks = () => {
            if (stream) {
                stream.getTracks().forEach((track) => track.stop());
                stream = null;
            }
        };

        const stopRecording = () => {
            if (recordTimer) {
                clearTimeout(recordTimer);
                recordTimer = null;
            }
            if (recorder && recorder.state !== "inactive") {
                recorder.stop();
            }
        };

        const currentItem = () => fields[currentIndex];

        const promptText = (item) => {
            const requiredText = item.required
                ? " This field is required."
                : " Say skip if this does not apply.";
            const dateText =
                item.field.type === "date"
                    ? " Please state the date as year, month and day, for example 2026-09-28."
                    : "";
            const choicesText =
                item.field instanceof HTMLSelectElement
                    ? item.options.length <= 8
                        ? ` Available choices: ${item.options.join(", ")}.`
                        : " Choose one of the options displayed on screen."
                    : item.field.type === "checkbox"
                      ? " Answer yes or no."
                      : "";
            return `${item.section ? `${item.section}. ` : ""}${item.label}.${requiredText}${dateText}${choicesText}`;
        };

        const askCurrent = () => {
            if (currentIndex >= fields.length) {
                progress.textContent = "All form fields have been asked.";
                question.textContent =
                    "Guided voice entry is complete. Review the full form and press its Save button when ready.";
                choices.textContent = "";
                transcriptLabel.hidden = true;
                status.textContent =
                    "The form has not been saved. Use the normal Save button after reviewing all answers.";
                recordButton.hidden = true;
                confirmButton.hidden = true;
                skipButton.hidden = true;
                repeatButton.hidden = true;
                closeButton.textContent = "Close";
                stopTracks();
                if ("speechSynthesis" in window) {
                    window.speechSynthesis.cancel();
                    speakAmericanEnglish(
                        "All form questions are complete. Please review your answers and save the form when ready.",
                    );
                }
                return;
            }

            const item = currentItem();
            progress.textContent = `Question ${currentIndex + 1} of ${fields.length}`;
            question.textContent = promptText(item);
            choices.textContent =
                item.field instanceof HTMLSelectElement && item.options.length
                    ? `Choices: ${item.options.join(" · ")}`
                    : item.field.type === "checkbox"
                      ? "Choices: Yes · No"
                      : "";
            transcript.value = "";
            transcriptLabel.hidden = true;
            confirmButton.hidden = true;
            recordButton.hidden = false;
            recordButton.disabled = !stream;
            skipButton.hidden = item.required;
            repeatButton.hidden = false;
            recordButton.textContent = stream
                ? "Start answer"
                : "Connect microphone";
            status.textContent = stream
                ? "Listen to the question, then answer when recording starts."
                : "Connect your microphone to begin. Audio is sent to this app for local transcription, then deleted.";

            if (stream && "speechSynthesis" in window) {
                window.speechSynthesis.cancel();
                const spokeQuestion = speakAmericanEnglish(promptText(item), {
                    onend: startRecording,
                    onerror: () => {
                        status.textContent =
                            "The question is shown above. Start your answer when ready.";
                    },
                });
                if (!spokeQuestion) {
                    status.textContent = missingEnglishVoiceMessage;
                }
            }
        };

        const advance = () => {
            currentIndex += 1;
            askCurrent();
        };

        const transcribeRecording = async () => {
            isRecording = false;
            recordButton.textContent = "Start answer";
            if (discardRecording) {
                discardRecording = false;
                chunks = [];
                return;
            }
            if (!chunks.length) {
                status.textContent = "No audio was recorded. Please try again.";
                recordButton.hidden = false;
                return;
            }
            const type = recorder.mimeType || "audio/webm";
            const extension =
                type.startsWith("audio/ogg")
                    ? "ogg"
                    : type.startsWith("audio/mp4")
                      ? "mp4"
                      : "webm";
            const audio = new Blob(chunks, { type });
            chunks = [];
            if (audio.size > MAX_AUDIO_BYTES) {
                status.textContent =
                    "This answer is too large. Please keep each answer under 90 seconds.";
                recordButton.hidden = false;
                return;
            }

            status.textContent = "Transcribing with the local speech model…";
            recordButton.disabled = true;
            const data = new FormData();
            data.append("audio", audio, `voice-answer.${extension}`);
            const csrf = form.querySelector('input[name="csrf_token"]')?.value;
            if (csrf) {
                data.append("csrf_token", csrf);
            }
            try {
                const response = await fetch("/voice/transcribe", {
                    method: "POST",
                    body: data,
                    credentials: "same-origin",
                });
                const result = await response.json();
                if (!response.ok) {
                    throw new Error(result.error || "Transcription failed.");
                }
                if (/^skip(?:\s|$)/.test(normalize(result.text))) {
                    if (currentItem().required) {
                        status.textContent =
                            "This field is required. Please provide an answer.";
                        recordButton.disabled = false;
                        return;
                    }
                    status.textContent =
                        "Optional field will be skipped. Confirm to continue.";
                    transcript.value = "skip";
                } else {
                    transcript.value = result.text;
                    status.textContent =
                        "Check or correct this transcript, then confirm to continue.";
                }
                transcriptLabel.hidden = false;
                confirmButton.hidden = false;
                recordButton.hidden = false;
                recordButton.disabled = false;
                recordButton.textContent = "Record answer again";
                skipButton.hidden = true;
                transcript.focus();
            } catch (error) {
                status.textContent =
                    error.message || "Transcription failed. Please try again.";
                recordButton.disabled = false;
            }
        };

        function startRecording() {
            if (!stream || isRecording || currentIndex >= fields.length) {
                return;
            }
            const mimeType = preferredMimeTypes.find((type) =>
                MediaRecorder.isTypeSupported(type),
            );
            recorder = mimeType
                ? new MediaRecorder(stream, { mimeType })
                : new MediaRecorder(stream);
            chunks = [];
            recorder.addEventListener("dataavailable", (event) => {
                if (event.data.size) {
                    chunks.push(event.data);
                }
            });
            recorder.addEventListener("stop", transcribeRecording, {
                once: true,
            });
            recorder.start();
            isRecording = true;
            transcriptLabel.hidden = true;
            confirmButton.hidden = true;
            recordButton.textContent = "Stop and transcribe";
            recordButton.disabled = false;
            status.textContent =
                "Recording your answer. Press “Stop and transcribe” when finished.";
            recordTimer = setTimeout(stopRecording, MAX_ANSWER_MS);
        }

        launch.addEventListener("click", () => {
            if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
                window.alert(
                    "Guided voice entry requires a browser with microphone support on localhost or a secure connection.",
                );
                return;
            }
            dialog.hidden = false;
            launch.disabled = true;
            status.textContent = "Requesting microphone access…";
            navigator.mediaDevices
                .getUserMedia({ audio: true })
                .then((microphoneStream) => {
                    stream = microphoneStream;
                    recordButton.disabled = false;
                    askCurrent();
                })
                .catch((error) => {
                    status.textContent =
                        error.name === "NotAllowedError"
                            ? "Microphone access was denied. Allow microphone access to continue."
                            : "The microphone could not be started. Check the device and try again.";
                    launch.disabled = false;
                });
        });

        recordButton.addEventListener("click", () => {
            if (isRecording) {
                stopRecording();
            } else if (!stream) {
                status.textContent =
                    "Microphone access is not available. Close the guide and start again.";
            } else {
                startRecording();
            }
        });

        confirmButton.addEventListener("click", () => {
            const answer = transcript.value.trim();
            if (/^skip(?:\s|$)/.test(normalize(answer))) {
                if (currentItem().required) {
                    status.textContent =
                        "This field is required. Please provide an answer.";
                    return;
                }
                advance();
                return;
            }
            if (!answer) {
                status.textContent = currentItem().required
                    ? "This field is required. Please provide an answer."
                    : "Provide an answer or say “skip” to leave this field blank.";
                return;
            }
            const error = setAnswer(currentItem(), answer);
            if (error) {
                status.textContent = error;
                return;
            }
            advance();
        });

        skipButton.addEventListener("click", () => {
            if (!currentItem().required) {
                advance();
            }
        });

        repeatButton.addEventListener("click", () => {
            const item = currentItem();
            if ("speechSynthesis" in window) {
                if (isRecording) {
                    discardRecording = true;
                    chunks = [];
                    stopRecording();
                }
                window.speechSynthesis.cancel();
                const spokeQuestion = speakAmericanEnglish(promptText(item), {
                    onend: startRecording,
                });
                if (!spokeQuestion) {
                    status.textContent = missingEnglishVoiceMessage;
                }
            }
        });

        closeButton.addEventListener("click", () => {
            discardRecording = isRecording;
            if (isRecording) {
                chunks = [];
            }
            stopRecording();
            if ("speechSynthesis" in window) {
                window.speechSynthesis.cancel();
            }
            stopTracks();
            dialog.hidden = true;
            launch.disabled = false;
            launch.focus();
        });

        window.addEventListener("beforeunload", () => {
            stopRecording();
            stopTracks();
            if ("speechSynthesis" in window) {
                window.speechSynthesis.cancel();
            }
        });
    }

    document.addEventListener("DOMContentLoaded", () => {
        document
            .querySelectorAll("form[data-guided-voice]")
            .forEach(setupGuidedVoice);
    });
})();
