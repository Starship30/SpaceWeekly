/* Experimental public ExtendScript host calls only. No QE or task replay. */
$._swSingleCard = (function () {
    function line(status, stage, message) {
        return status + "\t" + stage + "\t" + encodeURIComponent(String(message)) + "\n";
    }
    function target() {
        if (String(app.version).indexOf("26.3.") !== 0) throw Error("Only Premiere 26.3 is allowed");
        var project = app.project, sequence = project && project.activeSequence;
        if (!project || !sequence || !project.path) throw Error("Open a saved project and an active dedicated sequence");
        return { project: project, sequence: sequence };
    }
    function empty(sequence) {
        if (!sequence.videoTracks.numTracks || !sequence.audioTracks.numTracks) throw Error("V1 and A1 are required");
        var groups = [sequence.videoTracks, sequence.audioTracks];
        for (var g = 0; g < groups.length; g++) {
            for (var i = 0; i < groups[g].numTracks; i++) {
                if (groups[g][i].clips.numItems || groups[g][i].transitions.numItems) throw Error("Sequence must be empty");
            }
        }
        if (sequence.captionTracks && sequence.captionTracks.numTracks) throw Error("Caption tracks are not allowed");
    }
    function bind() {
        try {
            var t = target(); empty(t.sequence);
            var values = ["OK", app.version, app.build, t.project.path, t.sequence.sequenceID, t.sequence.name, t.sequence.timebase];
            for (var i = 0; i < values.length; i++) values[i] = encodeURIComponent(String(values[i]));
            return values.join("\t");
        } catch (e) { return "ERROR\t" + encodeURIComponent(String(e)); }
    }
    function inspect(mapping) {
        var report = "";
        try {
            var t = target(), sequence = t.sequence, count = 0, clip = null;
            var groups = [sequence.videoTracks, sequence.audioTracks];
            for (var g = 0; g < groups.length; g++) {
                for (var i = 0; i < groups[g].numTracks; i++) {
                    count += groups[g][i].clips.numItems;
                    if (g === 0 && i === 0 && groups[g][i].clips.numItems === 1) clip = groups[g][i].clips[0];
                }
            }
            if (count !== 1 || !clip) throw Error("Read-only inspection requires exactly one clip on V1");
            report += line("INFO", "target", t.project.path + "; " + sequence.sequenceID + "; nodeId=" + clip.nodeId);
            var component = clip.getMGTComponent();
            if (!component) throw Error("No MGT component");
            var params = component.properties, seen = {};
            for (var p = 0; p < params.numItems; p++) {
                var param = params[p], name = String(param.displayName);
                if (name !== mapping.title && name !== mapping.summary) continue;
                if (seen[name]) throw Error("Duplicate control: " + name);
                seen[name] = true;
                var value = param.getValue();
                report += line("INFO", "value-" + name, typeof value + ": " + String(value));
            }
            if (!seen[mapping.title] || !seen[mapping.summary]) throw Error("Missing text control");
            report += line("PASS", "inspect", "Read only; no timeline or parameter changes");
        } catch (e) { report += line("FAIL", "inspect", String(e)); }
        return report;
    }
    function run(request) {
        var report = "", stage = "target", clip = null;
        function sameTarget() {
            var current = target();
            if (current.project.path !== request.path || String(current.sequence.sequenceID) !== request.sequenceID) throw Error("Bound target changed");
            return current;
        }
        try {
            var t = sameTarget(), sequence = t.sequence;
            empty(sequence);
            if (String(sequence.timebase) !== request.timebase) throw Error("Sequence frame rate changed");
            if (!new File(request.mogrt).exists) throw Error("MOGRT file is missing");
            if (typeof sequence.importMGT !== "function") throw Error("importMGT is unavailable");
            report += line("PASS", stage, app.version + " build " + app.build + "; " + sequence.name);
            stage = "insert";
            sameTarget();
            clip = sequence.importMGT(request.mogrt, "0", 0, 0);
            if (!clip) throw Error("importMGT returned no clip; inspect timeline manually");
            if (sequence.videoTracks[0].clips.numItems !== 1) throw Error("Unexpected video clip count");
            for (var a = 0; a < sequence.audioTracks.numTracks; a++) {
                if (sequence.audioTracks[a].clips.numItems) throw Error("Template inserted audio");
            }
            report += line("PASS", stage, "One MOGRT inserted; nodeId=" + clip.nodeId);
            stage = "controls";
            sameTarget();
            var component = clip.getMGTComponent();
            if (!component) throw Error("getMGTComponent returned no component");
            var params = component.properties, found = {}, names = [];
            for (var p = 0; p < params.numItems; p++) {
                var param = params[p], name = String(param.displayName);
                names.push(name);
                if (name !== request.mapping.title && name !== request.mapping.summary) continue;
                if (found[name]) throw Error("Duplicate control: " + name);
                found[name] = param;
            }
            report += line("INFO", stage, "Names: " + names.join(", "));
            var fields = ["title", "summary"];
            // Validate both controls before the first text mutation.
            for (var f = 0; f < fields.length; f++) {
                var key = request.mapping[fields[f]], control = found[key];
                if (!control) throw Error("Missing control: " + key);
                if (control.isTimeVarying()) throw Error("Time-varying control: " + key);
                var value = control.getValue();
                report += line("INFO", stage, key + ": " + typeof value);
                if (typeof value !== "string") throw Error("Non-string control: " + key);
                report += line("INFO", "initial-" + fields[f], value);
            }
            report += line("PASS", stage, "Unique title and summary string parameters; actual write support still requires readback");
            for (var w = 0; w < fields.length; w++) {
                var field = fields[w], mapped = request.mapping[field];
                stage = "write-" + field; sameTarget();
                var result = found[mapped].setValue(request.item[field], true);
                if (result === false || (typeof result === "number" && result !== 0)) throw Error("setValue failed: " + result);
                report += line("INFO", stage, "setValue returned " + String(result) + "; checking actual text next");
                stage = "readback-" + field;
                var actual = found[mapped].getValue();
                report += line("INFO", stage, typeof actual + ": " + String(actual));
                if (actual !== request.item[field]) throw Error("Text readback is not the expected plain string: " + mapped + "; inspect logged value; stopping before next write");
                report += line("PASS", stage, request.item[field]);
            }
            stage = "duration"; sameTarget();
            var end = new Time(); end.ticks = request.endTicks;
            clip.end = end;
            if (String(clip.start.ticks) !== "0" || String(clip.end.ticks) !== request.endTicks) throw Error("Start/end ticks readback mismatch");
            report += line("PASS", stage, request.frames + " frames; end=" + clip.end.ticks);
            report += line("PASS", "complete", "Single card only; project not saved; visually inspect Chinese text and style");
        } catch (e) {
            report += line("FAIL", stage, String(e) + "; inserted=" + Boolean(clip) + "; no retry/rollback");
        }
        return report;
    }
    return { bind: bind, run: run, inspect: inspect };
})();
