package io.craftmcp.debugger;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.function.Predicate;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.Timeout;
import static org.junit.jupiter.api.Assertions.*;

@Timeout(30)
class JdiIntegrationTest {
    private static final String CLASS_NAME = "io.craftmcp.fixture.Calculator";

    @Test void pendingBreakpointStackLocalsFieldsArraysAndStaleReferences() throws Exception {
        try (Harness h = new Harness(false)) {
            String session = h.attach(15000);
            assertEquals("already_attached", h.error("attach_debugger", h.attachParams(15000)));
            assertEquals("not_suspended", h.error("get_stack_trace", Json.object("session_id", session,
                    "stop_id", "unknown", "thread_id", "unknown", "offset", 0, "limit", 10)));
            JsonObject bp = h.breakpoint(session, h.line(), null);
            assertEquals("pending", bp.get("status").getAsString());
            h.target.send("run");
            JsonObject stop = h.event("stopped").getAsJsonObject("data");
            assertEquals("event_thread", stop.get("suspend_policy").getAsString());
            JsonObject state = h.ok("get_debug_state", Json.object("session_id", session));
            assertEquals(stop.get("stop_id"), state.getAsJsonObject("active_stop").get("stop_id"));
            JsonObject page = h.stack(session, stop, 1);
            assertNotNull(page.get("next_offset"));
            assertTrue(page.get("truncated").getAsBoolean());
            JsonObject frame = page.getAsJsonArray("frames").get(0).getAsJsonObject();
            assertEquals(CLASS_NAME, frame.get("class_name").getAsString());
            assertEquals(h.line(), frame.get("line").getAsInt());
            String frameId = frame.get("frame_id").getAsString();
            assertEquals(frameId, h.stack(session, stop, 100).getAsJsonArray("frames").get(0)
                    .getAsJsonObject().get("frame_id").getAsString());
            JsonObject variables = h.locals(session, stop, frameId, 0, 100);
            assertEquals("12", named(variables, "damage").get("display_value").getAsString());
            assertEquals("4", named(variables, "base").get("display_value").getAsString());
            assertTrue(named(variables, "large").get("truncated").getAsBoolean());
            assertEquals(4096, named(variables, "large").get("display_value").getAsString().length());
            JsonObject localPage = h.locals(session, stop, frameId, 0, 1);
            assertEquals(1, localPage.getAsJsonArray("variables").size());
            assertEquals(1, localPage.get("next_offset").getAsInt());
            String receiver = named(variables, "this").get("object_ref").getAsString();
            assertEquals(receiver, named(variables, "target").get("object_ref").getAsString());
            JsonObject fields = h.fields(session, stop, receiver, 0, 100);
            assertEquals("테스트 대상\n두 번째 줄", named(fields, "label").get("display_value").getAsString());
            assertEquals(receiver, named(fields, "self").get("object_ref").getAsString());
            String wide = named(fields, "wide").get("object_ref").getAsString();
            JsonObject bounded = h.fields(session, stop, wide, 0, 100);
            assertTrue(Json.bytes(bounded) < 512 * 1024);
            assertTrue(bounded.get("next_offset").getAsInt() > 0);
            assertTrue(bounded.getAsJsonArray("variables").size() < 100);
            assertTrue(bounded.get("truncated").getAsBoolean());
            String array = named(variables, "values").get("object_ref").getAsString();
            JsonObject items = h.fields(session, stop, array, 1, 1);
            assertEquals("3", named(items, "[1]").get("display_value").getAsString());
            assertEquals(2, items.get("next_offset").getAsInt());
            assertEquals("invalid_params", h.error("get_variables", h.variableParams(session, stop, "frame_id", frameId, 0, 101)));
            assertEquals("unsupported_method", h.error("evaluate", Json.object()));
            assertEquals("invalid_params", h.error("get_debug_state", Json.object("session_id", session, "extra", true)));
            h.ok("resume_execution", Json.object("session_id", session, "stop_id", stop.get("stop_id")));
            assertTrue(h.target.await(line -> line.startsWith("RESULT damage=12"), 5000).startsWith("RESULT"));
            assertEquals("stale_reference", h.error("get_variables", h.variableParams(session, stop, "frame_id", frameId, 0, 10)));
            assertEquals("stale_reference", h.error("get_variables", h.variableParams(session, stop, "object_ref", receiver, 0, 10)));
            h.ok("remove_breakpoint", Json.object("session_id", session, "breakpoint_id", bp.get("breakpoint_id")));
            assertEquals("breakpoint_not_found", h.error("remove_breakpoint", Json.object("session_id", session, "breakpoint_id", bp.get("breakpoint_id"))));
            assertEquals("rejected", h.breakpoint(session, 99999, null).get("status").getAsString());
            h.ok("detach_debugger", Json.object("session_id", session));
            assertEquals("disconnected", h.ok("get_debug_state", Json.object("session_id", session)).get("connection_state").getAsString());
            // address=:0 selects a new ephemeral port when HotSpot reopens its JDWP listener.
            String listening = h.target.await(line -> line.startsWith("Listening for transport"), 5000);
            h.port = Integer.parseInt(listening.substring(listening.lastIndexOf(' ') + 1));
            String next = h.attach(1000);
            assertNotEquals(session, next);
            assertEquals("session_not_found", h.error("get_debug_state", Json.object("session_id", session)));
        }
    }

    @Test void leaseExpiryAutomaticallyResumesAndInvalidatesStop() throws Exception {
        try (Harness h = new Harness(false)) {
            String session = h.attach(400);
            h.breakpoint(session, h.line(), null);
            h.target.send("run");
            JsonObject stop = h.event("stopped").getAsJsonObject("data");
            JsonObject resumed = h.event("resumed").getAsJsonObject("data");
            assertEquals("lease_expired", resumed.get("reason").getAsString());
            assertEquals(stop.get("stop_id"), resumed.get("stop_id"));
            h.target.await(line -> line.startsWith("RESULT damage=12"), 5000);
            assertTrue(h.ok("get_debug_state", Json.object("session_id", session)).get("active_stop").isJsonNull());
            assertEquals("stale_reference", h.error("resume_execution", Json.object("session_id", session, "stop_id", stop.get("stop_id"))));
        }
    }

    @Test void stdinEofReleasesAnActiveStop() throws Exception {
        try (Harness h = new Harness(false)) {
            String session = h.attach(15000);
            h.breakpoint(session, h.line(), null);
            h.target.send("run");
            h.event("stopped");
            h.helper.input.close();
            assertTrue(h.helper.process.waitFor(5, TimeUnit.SECONDS));
            assertEquals(0, h.helper.process.exitValue(), h.helper.errors.toString());
            h.target.await(line -> line.startsWith("RESULT damage=12"), 5000);
        }
    }

    @Test void shutdownReleasesStopBeforeAcknowledging() throws Exception {
        try (Harness h = new Harness(false)) {
            String session = h.attach(15000);
            h.breakpoint(session, h.line(), null);
            h.target.send("run");
            h.event("stopped");
            assertTrue(h.ok("shutdown", Json.object()).get("shutdown").getAsBoolean());
            assertTrue(h.helper.process.waitFor(5, TimeUnit.SECONDS));
            assertEquals(0, h.helper.process.exitValue());
            h.target.await(line -> line.startsWith("RESULT damage=12"), 5000);
        }
    }

    @Test void detachReleasesStopAndTargetCanKeepRunning() throws Exception {
        try (Harness h = new Harness(false)) {
            String session = h.attach(15000);
            h.breakpoint(session, h.line(), null);
            h.target.send("run");
            JsonObject stop = h.event("stopped").getAsJsonObject("data");
            JsonObject result = h.ok("detach_debugger", Json.object("session_id", session));
            assertEquals(stop.get("stop_id"), result.getAsJsonArray("released_stop_ids").get(0));
            h.target.await(line -> line.startsWith("RESULT damage=12"), 5000);
            h.target.send("run");
            h.target.await(line -> line.startsWith("RESULT damage=12"), 5000);
        }
    }

    @Test void extraThreadStopIsCleanedWithoutReleasingTheFirst() throws Exception {
        try (Harness h = new Harness(false)) {
            String session = h.attach(15000);
            h.breakpoint(session, h.line(), null);
            h.target.send("parallel");
            JsonObject first = h.event("stopped").getAsJsonObject("data");
            JsonObject second = h.event("stopped").getAsJsonObject("data");
            JsonObject cleanup = h.event("resumed").getAsJsonObject("data");
            assertNotEquals(first.get("stop_id"), second.get("stop_id"));
            assertEquals(second.get("stop_id"), cleanup.get("stop_id"));
            assertEquals("extra_stop_cleanup", cleanup.get("reason").getAsString());
            JsonObject state = h.ok("get_debug_state", Json.object("session_id", session));
            assertEquals(first.get("stop_id"), state.getAsJsonObject("active_stop").get("stop_id"));
            h.ok("resume_execution", Json.object("session_id", session, "stop_id", first.get("stop_id")));
            h.target.await(line -> line.startsWith("RESULT"), 5000);
            h.target.await(line -> line.startsWith("RESULT"), 5000);
        }
    }

    @Test void vmTerminationIsReportedAndOldSessionIsNotCurrent() throws Exception {
        try (Harness h = new Harness(false)) {
            String session = h.attach(15000);
            h.target.send("quit");
            assertEquals("vm_terminated", h.event("disconnected").getAsJsonObject("data").get("reason").getAsString());
            assertEquals("terminated", h.ok("get_debug_state", Json.object("session_id", session)).get("connection_state").getAsString());
            assertEquals("vm_disconnected", h.error("remove_breakpoint", Json.object("session_id", session, "breakpoint_id", "unknown")));
        }
    }

    @Test void missingLocalMetadataIsAnExplicitErrorWithArgumentFallback() throws Exception {
        try (Harness h = new Harness(true)) {
            String session = h.attach(15000);
            h.breakpoint(session, h.line(), null);
            h.target.send("run");
            JsonObject stop = h.event("stopped").getAsJsonObject("data");
            String frame = h.stack(session, stop, 100).getAsJsonArray("frames").get(0).getAsJsonObject().get("frame_id").getAsString();
            JsonObject response = h.request("get_variables", h.variableParams(session, stop, "frame_id", frame, 0, 100));
            assertEquals("debug_info_missing", response.getAsJsonObject("error").get("code").getAsString());
            assertEquals(2, response.getAsJsonObject("error").getAsJsonObject("details").getAsJsonArray("argument_values").size());
        }
    }

    @Test void duplicateClassLoaderInvalidatesUnqualifiedBreakpoint() throws Exception {
        try (Harness h = new Harness(false)) {
            String session = h.attach(15000);
            h.breakpoint(session, h.line(), null);
            h.target.send("run");
            JsonObject stop = h.event("stopped").getAsJsonObject("data");
            h.ok("resume_execution", Json.object("session_id", session, "stop_id", stop.get("stop_id")));
            h.target.await(line -> line.startsWith("RESULT"), 5000);
            h.target.send("duplicate");
            h.target.await(line -> line.equals("DUPLICATE_LOADED"), 5000);
            JsonObject state = h.ok("get_debug_state", Json.object("session_id", session));
            assertEquals("rejected", state.getAsJsonArray("breakpoints").get(0).getAsJsonObject().get("status").getAsString());
            JsonObject failure = h.request("set_breakpoint", Json.object("session_id", session, "class_name", CLASS_NAME, "line", h.line()));
            assertEquals("ambiguous_class", failure.getAsJsonObject("error").get("code").getAsString());
            String loader = failure.getAsJsonObject("error").getAsJsonObject("details").getAsJsonArray("class_loader_ids").get(0).getAsString();
            assertEquals("verified", h.breakpoint(session, h.line(), loader).get("status").getAsString());
        }
    }

    @Test void helperForceTerminationDoesNotLeaveTargetSuspended() throws Exception {
        try (Harness h = new Harness(false)) {
            String session = h.attach(15000);
            h.breakpoint(session, h.line(), null);
            h.target.send("run");
            h.event("stopped");
            h.helper.process.destroyForcibly();
            assertTrue(h.helper.process.waitFor(5, TimeUnit.SECONDS));
            h.target.await(line -> line.startsWith("RESULT damage=12"), 5000);
        }
    }

    @Test void malformedJsonAndProtocolVersionAreRejected() throws Exception {
        for (String wire : List.of("not json", "{\"protocol_version\":2,\"type\":\"request\",\"id\":\"x\",\"method\":\"shutdown\",\"params\":{}}")) {
            try (Peer helper = helper()) {
                helper.await(line -> line.contains("\"ready\""), 5000);
                helper.send(wire);
                assertTrue(helper.process.waitFor(5, TimeUnit.SECONDS));
                assertEquals(2, helper.process.exitValue());
                assertTrue(helper.errors.length() > 0);
            }
        }
    }

    @Test void twoBreakpointsAtOneLocationProduceOneOwnedStop() throws Exception {
        try (Harness h = new Harness(false)) {
            String session = h.attach(15000);
            h.breakpoint(session, h.line(), null);
            h.breakpoint(session, h.line(), null);
            h.target.send("run");
            JsonObject stop = h.event("stopped").getAsJsonObject("data");
            h.ok("resume_execution", Json.object("session_id", session, "stop_id", stop.get("stop_id")));
            h.target.await(line -> line.startsWith("RESULT damage=12"), 5000);
            assertTrue(h.ok("get_debug_state", Json.object("session_id", session)).get("active_stop").isJsonNull());
            assertTrue(h.messages.stream().noneMatch(message -> message.has("event") && message.get("event").getAsString().equals("stopped")));
        }
    }

    @Test void malformedInputDuringStopStillReleasesTarget() throws Exception {
        try (Harness h = new Harness(false)) {
            String session = h.attach(15000);
            h.breakpoint(session, h.line(), null);
            h.target.send("run");
            h.event("stopped");
            h.helper.send("not json");
            assertTrue(h.helper.process.waitFor(5, TimeUnit.SECONDS));
            assertEquals(2, h.helper.process.exitValue());
            h.target.await(line -> line.startsWith("RESULT damage=12"), 5000);
        }
    }

    private static JsonObject named(JsonObject page, String name) {
        for (JsonElement element : page.getAsJsonArray("variables")) {
            JsonObject row = element.getAsJsonObject();
            if (row.get("name").getAsString().equals(name)) return row;
        }
        throw new AssertionError("Missing variable " + name + " in " + page);
    }

    private static String java() {
        return Path.of(System.getProperty("java.home"), "bin", System.getProperty("os.name").startsWith("Windows") ? "java.exe" : "java").toString();
    }

    private static Peer helper() throws Exception {
        return new Peer(new ProcessBuilder(java(), "--add-modules", "jdk.jdi", "-jar", System.getProperty("helper.jar")).start());
    }

    private static final class Harness implements AutoCloseable {
        final Peer target;
        final Peer helper;
        final List<JsonObject> messages = new ArrayList<>();
        int port;
        int requestId;

        Harness(boolean noVars) throws Exception {
            target = new Peer(new ProcessBuilder(java(), "-agentlib:jdwp=transport=dt_socket,server=y,suspend=n,address=127.0.0.1:0",
                    "-cp", System.getProperty(noVars ? "debuggee.noVars" : "debuggee.classes"), "io.craftmcp.fixture.Debuggee").start());
            try {
                String listening = target.await(line -> line.startsWith("Listening for transport"), 5000);
                port = Integer.parseInt(listening.substring(listening.lastIndexOf(' ') + 1));
                target.await(line -> line.equals("READY"), 5000);
                helper = helper();
                take(message -> message.has("event") && message.get("event").getAsString().equals("ready"));
            } catch (Throwable failure) { target.close(); throw failure; }
        }

        int line() throws Exception {
            List<String> source = Files.readAllLines(Path.of(System.getProperty("debuggee.source")));
            for (int i = 0; i < source.size(); i++) if (source.get(i).contains("BREAKPOINT_AFTER_CALC")) return i + 1;
            throw new AssertionError("Breakpoint marker missing.");
        }

        JsonObject attachParams(int lease) { return Json.object("host", "127.0.0.1", "port", port, "connect_timeout_ms", 3000, "stop_timeout_ms", lease); }
        String attach(int lease) throws Exception { return ok("attach_debugger", attachParams(lease)).get("session_id").getAsString(); }
        JsonObject breakpoint(String session, int line, String loader) throws Exception {
            JsonObject p = Json.object("session_id", session, "class_name", CLASS_NAME, "line", line);
            if (loader != null) p.addProperty("class_loader_id", loader);
            return ok("set_breakpoint", p);
        }

        JsonObject stack(String session, JsonObject stop, int limit) throws Exception {
            return ok("get_stack_trace", Json.object("session_id", session, "stop_id", stop.get("stop_id"),
                    "thread_id", stop.get("thread_id"), "offset", 0, "limit", limit));
        }

        JsonObject variableParams(String session, JsonObject stop, String selector, String reference, int offset, int limit) {
            return Json.object("session_id", session, "stop_id", stop.get("stop_id"), selector, reference, "offset", offset, "limit", limit);
        }
        JsonObject locals(String session, JsonObject stop, String frame, int offset, int limit) throws Exception {
            return ok("get_variables", variableParams(session, stop, "frame_id", frame, offset, limit));
        }
        JsonObject fields(String session, JsonObject stop, String reference, int offset, int limit) throws Exception {
            return ok("get_variables", variableParams(session, stop, "object_ref", reference, offset, limit));
        }
        JsonObject request(String method, JsonObject params) throws Exception {
            String id = "test-" + ++requestId;
            helper.send(Json.GSON.toJson(Json.object("protocol_version", 1, "type", "request", "id", id, "method", method, "params", params)));
            return take(message -> message.has("id") && message.get("id").getAsString().equals(id));
        }
        JsonObject ok(String method, JsonObject params) throws Exception {
            JsonObject message = request(method, params);
            assertTrue(message.get("ok").getAsBoolean(), message.toString());
            return message.getAsJsonObject("result");
        }
        String error(String method, JsonObject params) throws Exception {
            JsonObject message = request(method, params);
            assertFalse(message.get("ok").getAsBoolean(), message.toString());
            return message.getAsJsonObject("error").get("code").getAsString();
        }
        JsonObject event(String name) throws Exception {
            return take(message -> message.has("event") && message.get("event").getAsString().equals(name));
        }
        JsonObject take(Predicate<JsonObject> predicate) throws Exception {
            long end = System.nanoTime() + Duration.ofSeconds(10).toNanos();
            while (true) {
                for (int i = 0; i < messages.size(); i++) if (predicate.test(messages.get(i))) return messages.remove(i);
                long remaining = end - System.nanoTime();
                if (remaining <= 0) throw new AssertionError("Helper message timeout; queued=" + messages + "; stderr=" + helper.errors);
                String line = helper.lines.poll(remaining, TimeUnit.NANOSECONDS);
                if (line == null) throw new AssertionError("Helper stdout timeout; stderr=" + helper.errors);
                messages.add(Json.GSON.fromJson(line, JsonObject.class));
            }
        }
        @Override public void close() throws Exception { try { helper.close(); } finally { target.close(); } }
    }

    private static final class Peer implements AutoCloseable {
        final Process process;
        final BufferedWriter input;
        final BlockingQueue<String> lines = new LinkedBlockingQueue<>();
        final StringBuffer errors = new StringBuffer();

        Peer(Process process) {
            this.process = process;
            input = new BufferedWriter(new OutputStreamWriter(process.getOutputStream(), StandardCharsets.UTF_8));
            pump(process.getInputStream(), lines::add);
            pump(process.getErrorStream(), line -> errors.append(line).append('\n'));
        }
        void pump(java.io.InputStream stream, java.util.function.Consumer<String> consumer) {
            Thread reader = new Thread(() -> {
                try (var input = new BufferedReader(new InputStreamReader(stream, StandardCharsets.UTF_8))) {
                    String line;
                    while ((line = input.readLine()) != null) consumer.accept(line);
                } catch (java.io.IOException ignored) {}
            });
            reader.setDaemon(true);
            reader.start();
        }
        void send(String line) throws Exception { input.write(line); input.newLine(); input.flush(); }
        String await(Predicate<String> predicate, int millis) throws Exception {
            long end = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(millis);
            while (true) {
                long remaining = end - System.nanoTime();
                if (remaining <= 0) throw new AssertionError("Process stdout timeout; stderr=" + errors);
                String line = lines.poll(remaining, TimeUnit.NANOSECONDS);
                if (line == null) throw new AssertionError("Process stdout timeout; stderr=" + errors);
                if (predicate.test(line)) return line;
            }
        }
        @Override public void close() throws Exception {
            try { input.close(); } catch (java.io.IOException ignored) {}
            if (!process.waitFor(3, TimeUnit.SECONDS)) {
                process.destroyForcibly();
                assertTrue(process.waitFor(3, TimeUnit.SECONDS));
            }
        }
    }
}
