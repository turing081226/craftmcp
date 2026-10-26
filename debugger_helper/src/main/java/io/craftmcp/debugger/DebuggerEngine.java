package io.craftmcp.debugger;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.sun.jdi.*;
import com.sun.jdi.connect.AttachingConnector;
import com.sun.jdi.event.*;
import com.sun.jdi.request.BreakpointRequest;
import com.sun.jdi.request.ClassPrepareRequest;
import com.sun.jdi.request.EventRequest;
import java.time.Instant;
import java.util.*;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;

/** Owns all JDI state. Commands, event handling and leases use the same monitor. */
final class DebuggerEngine {
    private static final int PAGE_BUDGET = 480 * 1024; // Leaves room for the response envelope.
    private final Consumer<JsonElement> output;
    private final ScheduledExecutorService leases;
    private final Map<String, Breakpoint> breakpoints = new LinkedHashMap<>();
    private VirtualMachine vm;
    private String sessionId;
    private String connectionState = "disconnected";
    private long eventSeq;
    private int stopTimeoutMs;
    private Stop active;
    private boolean hasStopped;
    private boolean closed;

    DebuggerEngine(Consumer<JsonElement> output) {
        this.output = output;
        leases = Executors.newSingleThreadScheduledExecutor(task -> {
            Thread thread = new Thread(task, "debugger-lease");
            thread.setDaemon(true);
            return thread;
        });
        leases.scheduleWithFixedDelay(this::expireLease, 25, 25, TimeUnit.MILLISECONDS);
    }

    synchronized void ready() { event("ready", Json.object("helper_version", "0.1.0")); }

    synchronized JsonObject call(String method, JsonObject data) {
        if (closed) throw new Fault("internal_error", "Helper is shutting down.");
        try {
            return switch (method) {
                case "attach_debugger" -> attach(params(data, "host", "port", "connect_timeout_ms", "stop_timeout_ms"));
                case "set_breakpoint" -> setBreakpoint(new Params(data,
                        Set.of("session_id", "class_name", "line"), Set.of("source_path", "class_loader_id")));
                case "remove_breakpoint" -> removeBreakpoint(params(data, "session_id", "breakpoint_id"));
                case "get_debug_state" -> state(params(data, "session_id"));
                case "get_stack_trace" -> stack(params(data, "session_id", "stop_id", "thread_id", "offset", "limit"));
                case "get_variables" -> variables(new Params(data,
                        Set.of("session_id", "stop_id", "offset", "limit"), Set.of("frame_id", "object_ref")));
                case "resume_execution" -> resume(params(data, "session_id", "stop_id"));
                case "detach_debugger" -> detach(params(data, "session_id"));
                case "shutdown" -> {
                    params(data);
                    disconnect("shutdown");
                    yield Json.object("shutdown", true);
                }
                default -> throw new Fault("unsupported_method", "Unsupported method.");
            };
        } catch (Fault fault) { throw fault;
        } catch (VMDisconnectedException exception) {
            lost("connection_lost");
            throw new Fault("vm_disconnected", "Target JVM disconnected.");
        } catch (ObjectCollectedException exception) {
            throw new Fault("object_collected", "The target object was collected.");
        } catch (InvalidStackFrameException exception) {
            throw new Fault("stale_reference", "The frame is no longer valid.");
        } catch (IncompatibleThreadStateException exception) {
            throw new Fault("not_suspended", "The thread is not suspended.");
        } catch (Exception exception) {
            System.err.println("Debugger request failed: " + exception.getClass().getSimpleName());
            throw new Fault("internal_error", "Debugger operation failed.");
        }
    }

    private Params params(JsonObject data, String... names) { return new Params(data, Set.of(names), Set.of()); }

    private JsonObject attach(Params p) {
        String host = p.string("host");
        int port = p.integer("port", 1, 65535);
        int connectTimeout = p.integer("connect_timeout_ms", 1, Integer.MAX_VALUE);
        int leaseTimeout = p.integer("stop_timeout_ms", 1, Integer.MAX_VALUE);
        if (vm != null) throw new Fault("already_attached", "Detach the current JVM before attaching.");
        AttachingConnector connector = Bootstrap.virtualMachineManager().attachingConnectors().stream()
                .filter(candidate -> candidate.name().equals("com.sun.jdi.SocketAttach")).findFirst()
                .orElseThrow(() -> new Fault("attach_failed", "Socket attaching connector is unavailable."));
        var arguments = connector.defaultArguments();
        arguments.get("hostname").setValue(host);
        arguments.get("port").setValue(Integer.toString(port));
        arguments.get("timeout").setValue(Integer.toString(connectTimeout));
        try { vm = connector.attach(arguments);
        } catch (Exception exception) {
            throw new Fault("attach_failed", "Could not attach to the target JVM.");
        }
        sessionId = id("session");
        connectionState = "connected";
        stopTimeoutMs = leaseTimeout;
        active = null;
        hasStopped = false;
        breakpoints.clear();
        VirtualMachine attached = vm;
        Thread events = new Thread(() -> readEvents(attached), "debugger-events");
        events.setDaemon(true);
        events.start();
        return Json.object("session_id", sessionId, "connection_state", connectionState,
                "capabilities", Json.object("stack_trace", true, "variables", true,
                        "object_fields", true, "method_return_events", false));
    }

    private void requireSession(Params p, boolean connected) {
        if (!Objects.equals(sessionId, p.string("session_id")))
            throw new Fault("session_not_found", "Unknown debugger session.");
        if (connected && vm == null) throw new Fault("vm_disconnected", "The session is disconnected.");
    }

    private Stop requireStop(Params p) {
        requireSession(p, true);
        String stopId = p.string("stop_id");
        if (active == null) throw new Fault(hasStopped ? "stale_reference" : "not_suspended",
                "No matching active stop.");
        if (!active.id.equals(stopId)) throw new Fault("stale_reference", "Stop is no longer active.");
        // Requests do not extend the lease; expiry wins even if the timer was waiting on this monitor.
        if (System.nanoTime() - active.deadlineNanos >= 0) {
            release("lease_expired");
            throw new Fault("stale_reference", "Stop lease expired.");
        }
        return active;
    }

    private JsonObject setBreakpoint(Params p) throws AbsentInformationException {
        requireSession(p, true);
        String className = p.string("class_name");
        if (className.length() > 1024 || !className.matches("[\\p{L}_$][\\p{L}\\p{N}_$]*(\\.[\\p{L}_$][\\p{L}\\p{N}_$]*)*"))
            throw Params.invalid("Expected an exact fully qualified class name.");
        int line = p.integer("line", 1, Integer.MAX_VALUE);
        String loader = p.optionalString("class_loader_id");
        String source = p.optionalString("source_path");
        List<ReferenceType> matches = preparedClasses(className);
        if (loader == null && matches.size() > 1)
            throw new Fault("ambiguous_class", "Specify a class loader.",
                    Json.object("class_loader_ids", matches.stream().map(this::loaderId).toList()));
        Breakpoint bp = new Breakpoint(id("bp"), className, line, loader, source);
        ClassPrepareRequest prepare = vm.eventRequestManager().createClassPrepareRequest();
        prepare.addClassFilter(className);
        prepare.setSuspendPolicy(EventRequest.SUSPEND_EVENT_THREAD);
        prepare.enable();
        bp.prepare = prepare;
        breakpoints.put(bp.id, bp);
        try { refresh(bp);
        } catch (RuntimeException exception) {
            deleteRequests(bp);
            breakpoints.remove(bp.id);
            throw exception;
        }
        return bp.json();
    }

    private List<ReferenceType> preparedClasses(String name) {
        return vm.classesByName(name).stream().filter(ReferenceType::isPrepared).toList();
    }

    private String loaderId(ReferenceType type) {
        ClassLoaderReference loader = type.classLoader();
        return loader == null ? "loader-bootstrap" : "loader-" + loader.uniqueID();
    }

    private void refresh(Breakpoint bp) {
        List<ReferenceType> matches = preparedClasses(bp.className);
        if (bp.loader != null) matches = matches.stream().filter(type -> loaderId(type).equals(bp.loader)).toList();
        String previous = Json.GSON.toJson(bp.json());
        clearLocations(bp);
        if (matches.isEmpty()) {
            bp.status = "pending";
            bp.reason = "Matching class has not been prepared.";
        } else if (matches.size() > 1) {
            bp.status = "rejected";
            bp.reason = "ambiguous_class: specify a class loader.";
        } else {
            ReferenceType type = matches.get(0);
            try {
                List<Location> locations = type.locationsOfLine(bp.line);
                if (locations.isEmpty()) {
                    bp.status = "rejected";
                    bp.reason = "No executable location at the requested line.";
                } else {
                    for (Location location : new LinkedHashSet<>(locations)) {
                        BreakpointRequest request = vm.eventRequestManager().createBreakpointRequest(location);
                        request.putProperty("breakpoint_id", bp.id);
                        request.setSuspendPolicy(EventRequest.SUSPEND_EVENT_THREAD);
                        request.enable();
                        bp.requests.add(request);
                        JsonObject details = location(location);
                        details.addProperty("class_loader_id", loaderId(type));
                        bp.locations.add(details);
                    }
                    bp.status = "verified";
                    bp.reason = null;
                }
            } catch (AbsentInformationException exception) {
                bp.status = "rejected";
                bp.reason = "debug_info_missing: no line information.";
            }
        }
        if (!previous.equals(Json.GSON.toJson(bp.json()))) event("breakpoint_changed", bp.json());
    }

    private JsonObject removeBreakpoint(Params p) {
        requireSession(p, true);
        String breakpointId = p.string("breakpoint_id");
        Breakpoint bp = breakpoints.get(breakpointId);
        if (bp == null) throw new Fault("breakpoint_not_found", "Unknown breakpoint.");
        deleteRequests(bp);
        breakpoints.remove(breakpointId);
        return Json.object("breakpoint_id", breakpointId, "removed", true);
    }

    private void clearLocations(Breakpoint bp) {
        if (!bp.requests.isEmpty()) vm.eventRequestManager().deleteEventRequests(bp.requests);
        bp.requests.clear();
        bp.locations.clear();
    }

    private void deleteRequests(Breakpoint bp) {
        clearLocations(bp);
        if (bp.prepare != null) vm.eventRequestManager().deleteEventRequest(bp.prepare);
    }

    private JsonObject state(Params p) {
        requireSession(p, false);
        if (active != null && System.nanoTime() - active.deadlineNanos >= 0) release("lease_expired");
        return Json.object("connection_state", connectionState, "active_stop", active == null ? null : active.json(),
                "breakpoints", breakpoints.values().stream().map(Breakpoint::json).toList(), "last_event_seq", eventSeq);
    }

    private JsonObject stack(Params p) throws IncompatibleThreadStateException {
        Stop stop = requireStop(p);
        if (!stop.threadId.equals(p.string("thread_id"))) throw Params.invalid("Thread does not belong to this stop.");
        int offset = p.integer("offset", 0, Integer.MAX_VALUE);
        int limit = p.integer("limit", 1, 100);
        int total = stop.thread.frameCount();
        JsonArray frames = new JsonArray();
        int end = (int) Math.min(total, (long) offset + limit);
        int index = offset;
        for (; index < end; index++) {
            String reference = stop.frameIds.computeIfAbsent(index, ignored -> id("frame"));
            stop.frames.putIfAbsent(reference, index);
            JsonObject row = location(stop.thread.frame(index).location());
            row.addProperty("frame_id", reference);
            row.addProperty("index", index);
            frames.add(row);
            if (Json.bytes(frames) > PAGE_BUDGET) { frames.remove(frames.size() - 1); break; }
        }
        return Json.object("frames", frames, "next_offset", index < total ? index : null, "truncated", index < total);
    }

    private JsonObject location(Location location) {
        String source = null;
        try { source = location.sourceName(); } catch (AbsentInformationException ignored) {}
        int line = location.lineNumber();
        return Json.object("class_name", location.declaringType().name(), "method_name", location.method().name(),
                "source_name", source, "line", line < 0 ? null : line);
    }

    private JsonObject variables(Params p) throws IncompatibleThreadStateException {
        Stop stop = requireStop(p);
        if (p.has("frame_id") == p.has("object_ref")) throw Params.invalid("Specify exactly one frame_id or object_ref.");
        int offset = p.integer("offset", 0, Integer.MAX_VALUE);
        int limit = p.integer("limit", 1, 100);
        List<NamedValue> values;
        int total;
        if (p.has("frame_id")) {
            Integer frameIndex = stop.frames.get(p.string("frame_id"));
            if (frameIndex == null) throw new Fault("stale_reference", "Unknown frame reference.");
            StackFrame frame = stop.thread.frame(frameIndex);
            List<LocalVariable> locals;
            try { locals = frame.visibleVariables();
            } catch (AbsentInformationException exception) {
                // Argument values remain available without names even when LocalVariableTable is absent.
                var arguments = frame.getArgumentValues();
                JsonArray fallback = new JsonArray();
                int argumentIndex = offset;
                int end = (int) Math.min(arguments.size(), (long) offset + limit);
                for (; argumentIndex < end; argumentIndex++) {
                    fallback.add(variable(stop, "<argument-" + argumentIndex + ">", arguments.get(argumentIndex)));
                    if (Json.bytes(fallback) > PAGE_BUDGET) { fallback.remove(fallback.size() - 1); break; }
                }
                throw new Fault("debug_info_missing", "Local variable metadata is missing.",
                        Json.object("argument_values", fallback,
                                "argument_next_offset", argumentIndex < arguments.size() ? argumentIndex : null,
                                "this_value", frame.thisObject() == null ? null : variable(stop, "this", frame.thisObject())));
            }
            values = new ArrayList<>();
            ObjectReference receiver = frame.thisObject();
            if (receiver != null) values.add(new NamedValue("this", receiver));
            for (LocalVariable local : locals) values.add(new NamedValue(local.name(), frame.getValue(local)));
            total = values.size();
            int from = Math.min(offset, total);
            values = values.subList(from, (int) Math.min(total, (long) offset + limit));
        } else {
            ObjectReference object = stop.objects.get(p.string("object_ref"));
            if (object == null) throw new Fault("stale_reference", "Unknown object reference.");
            values = new ArrayList<>();
            if (object instanceof ArrayReference array) {
                total = array.length();
                int count = (int) Math.min(Math.max(0L, (long) total - offset), limit);
                List<Value> items = count == 0 ? List.of() : array.getValues(offset, count);
                for (int i = 0; i < items.size(); i++) values.add(new NamedValue("[" + (offset + i) + "]", items.get(i)));
            } else {
                List<Field> fields = object.referenceType().allFields().stream().filter(field -> !field.isStatic())
                        .sorted(Comparator.comparing(Field::name).thenComparing(field -> field.declaringType().name())).toList();
                total = fields.size();
                int end = (int) Math.min(total, (long) offset + limit);
                for (int i = offset; i < end; i++) {
                    Field field = fields.get(i);
                    values.add(new NamedValue(field.name(), object.getValue(field)));
                }
            }
        }
        JsonArray rows = new JsonArray();
        boolean clipped = false;
        int index = offset;
        for (NamedValue item : values) {
            JsonObject row = variable(stop, item.name, item.value);
            rows.add(row);
            if (Json.bytes(rows) > PAGE_BUDGET) { rows.remove(rows.size() - 1); break; }
            clipped |= row.get("truncated").getAsBoolean();
            index++;
        }
        return Json.object("variables", rows, "next_offset", index < total ? index : null,
                "truncated", clipped || index < total, "warnings", List.of());
    }

    private JsonObject variable(Stop stop, String name, Value value) {
        String kind = "null";
        String type = "null";
        String display = "null";
        String reference = null;
        boolean children = false;
        if (value != null) {
            type = value.type().name();
            if (value instanceof StringReference string) {
                kind = "string";
                display = string.value();
            } else if (value instanceof ObjectReference object) {
                kind = value instanceof ArrayReference ? "array" : "object";
                children = object instanceof ArrayReference array ? array.length() > 0
                        : object.referenceType().allFields().stream().anyMatch(field -> !field.isStatic());
                display = type + (object instanceof ArrayReference array ? "[length=" + array.length() + "]" : "");
                if (children) {
                    reference = stop.objectIds.computeIfAbsent(object.uniqueID(), ignored -> id("object"));
                    stop.objects.putIfAbsent(reference, object);
                }
            } else {
                kind = "primitive";
                display = value.toString(); // JDI primitive mirror; never invokes target Object.toString().
            }
        }
        boolean truncated = display.codePointCount(0, display.length()) > 4096;
        if (truncated) display = display.substring(0, display.offsetByCodePoints(0, 4096));
        return Json.object("name", name, "kind", kind, "type_name", type, "display_value", display,
                "object_ref", reference, "has_children", children, "truncated", truncated);
    }

    private JsonObject resume(Params p) {
        Stop stop = requireStop(p);
        String stopId = stop.id;
        release("user");
        return Json.object("resumed_stop_id", stopId);
    }

    private JsonObject detach(Params p) {
        requireSession(p, true);
        List<String> stops = active == null ? List.of() : List.of(active.id);
        disconnect("detach");
        return Json.object("detached", true, "released_stop_ids", stops);
    }

    private void release(String reason) {
        if (active == null) return;
        Stop stop = active;
        try { stop.batch.resume();
        } catch (VMDisconnectedException exception) {
            lost("connection_lost");
            throw new Fault("vm_disconnected", "Target JVM disconnected during resume.");
        } catch (RuntimeException exception) {
            throw new Fault("resume_failed", "Could not resume the event stop.");
        }
        active = null;
        event("resumed", Json.object("stop_id", stop.id, "reason", reason));
    }

    private void disconnect(String reason) {
        if (vm == null) return;
        release(reason);
        VirtualMachine attached = vm;
        try { attached.dispose();
        } catch (VMDisconnectedException ignored) {}
        lost(reason);
    }

    private void lost(String reason) {
        if (vm == null) return;
        vm = null;
        active = null;
        connectionState = reason.equals("vm_terminated") ? "terminated" : "disconnected";
        event("disconnected", Json.object("reason", reason));
    }

    private void readEvents(VirtualMachine attached) {
        while (true) {
            synchronized (this) { if (closed || vm != attached) return; }
            EventBatch batch = null;
            try {
                EventSet events = attached.eventQueue().remove(200);
                if (events == null) continue;
                batch = new EventBatch(events);
                synchronized (this) {
                    if (vm == attached && !closed) handleEvents(batch);
                }
            } catch (InterruptedException exception) {
                Thread.currentThread().interrupt();
                return;
            } catch (VMDisconnectedException exception) {
                synchronized (this) { if (vm == attached) lost("connection_lost"); }
                return;
            } catch (RuntimeException exception) {
                System.err.println("Debugger event failed: " + exception.getClass().getSimpleName());
            } finally {
                if (batch != null) {
                    synchronized (this) {
                        // Ownership survives output failures and lease expiry between monitor acquisitions.
                        if (!batch.retained) {
                            try { batch.resume();
                            } catch (VMDisconnectedException ignored) {
                            } catch (RuntimeException exception) {
                                System.err.println("Debugger event resume failed: " + exception.getClass().getSimpleName());
                                if (vm == attached) closeSafely();
                            }
                        }
                    }
                }
            }
        }
    }

    private void handleEvents(EventBatch batch) {
        EventSet events = batch.events;
        BreakpointEvent hit = null;
        for (Event event : events) {
            if (event instanceof VMDeathEvent) { lost("vm_terminated"); return; }
            if (event instanceof VMDisconnectEvent) { lost("connection_lost"); return; }
            if (event instanceof ClassPrepareEvent prepare) {
                for (Breakpoint bp : breakpoints.values()) if (bp.className.equals(prepare.referenceType().name())) refresh(bp);
            }
            if (event instanceof BreakpointEvent breakpoint && hit == null) {
                String breakpointId = (String) breakpoint.request().getProperty("breakpoint_id");
                Breakpoint registered = breakpoints.get(breakpointId);
                if (registered != null && registered.status.equals("verified")) hit = breakpoint;
            }
        }
        if (hit == null) return;
        Stop stop = new Stop(id("stop"), hit.thread(), batch, (String) hit.request().getProperty("breakpoint_id"), stopTimeoutMs);
        hasStopped = true;
        if (active != null) {
            event("stopped", stop.json());
            batch.resume();
            event("resumed", Json.object("stop_id", stop.id, "reason", "extra_stop_cleanup"));
        } else {
            active = stop;
            batch.retained = true;
            event("stopped", stop.json());
        }
    }

    private void expireLease() {
        synchronized (this) {
            if (closed || active == null || System.nanoTime() - active.deadlineNanos < 0) return;
            try { release("lease_expired");
            } catch (RuntimeException exception) {
                System.err.println("Debugger lease cleanup failed: " + exception.getClass().getSimpleName());
            }
        }
    }

    private void event(String name, JsonObject data) {
        output.accept(Json.object("protocol_version", 1, "type", "event", "seq", ++eventSeq,
                "event", name, "session_id", sessionId, "observed_at", Instant.now().toString(), "data", data));
    }

    synchronized boolean closeSafely() {
        if (closed) return true;
        try {
            disconnect("shutdown");
            closed = true;
            leases.shutdownNow();
            return true;
        } catch (RuntimeException exception) {
            System.err.println("Debugger cleanup failed: " + exception.getClass().getSimpleName());
            // Try disposal even if event resume or the stdout pipe failed.
            try { if (vm != null) vm.dispose(); } catch (RuntimeException ignored) {}
            vm = null;
            active = null;
            closed = true;
            leases.shutdownNow();
            return false;
        }
    }

    void close() { closeSafely(); }

    private static String id(String prefix) { return prefix + "-" + UUID.randomUUID(); }

    private record NamedValue(String name, Value value) {}

    /** Each EventSet owns exactly one matching resume, even if protocol output throws. */
    private static final class EventBatch {
        final EventSet events;
        boolean retained;
        boolean resumed;

        EventBatch(EventSet events) { this.events = events; }

        void resume() {
            if (!resumed) {
                events.resume();
                resumed = true;
            }
        }
    }

    private static final class Breakpoint {
        final String id;
        final String className;
        final int line;
        final String loader;
        final String source;
        final List<BreakpointRequest> requests = new ArrayList<>();
        final List<JsonObject> locations = new ArrayList<>();
        ClassPrepareRequest prepare;
        String status = "pending";
        String reason;

        Breakpoint(String id, String className, int line, String loader, String source) {
            this.id = id;
            this.className = className;
            this.line = line;
            this.loader = loader;
            this.source = source;
        }

        JsonObject json() {
            return Json.object("breakpoint_id", id, "status", status, "locations", locations, "reason", reason);
        }
    }

    private static final class Stop {
        final String id;
        final ThreadReference thread;
        final String threadId;
        final EventBatch batch;
        final String breakpointId;
        final long deadlineNanos;
        final Instant observedAt = Instant.now();
        final Instant expiresAt;
        final Map<Integer, String> frameIds = new HashMap<>();
        final Map<String, Integer> frames = new HashMap<>();
        final Map<Long, String> objectIds = new HashMap<>();
        final Map<String, ObjectReference> objects = new HashMap<>();

        Stop(String id, ThreadReference thread, EventBatch batch, String breakpointId, int timeout) {
            this.id = id;
            this.thread = thread;
            this.threadId = "thread-" + thread.uniqueID();
            this.batch = batch;
            this.breakpointId = breakpointId;
            this.deadlineNanos = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(timeout);
            this.expiresAt = observedAt.plusMillis(timeout);
        }

        JsonObject json() {
            String policy = switch (batch.events.suspendPolicy()) {
                case EventRequest.SUSPEND_ALL -> "all";
                case EventRequest.SUSPEND_EVENT_THREAD -> "event_thread";
                default -> "none";
            };
            return Json.object("stop_id", id, "thread_id", threadId, "reason", "breakpoint",
                    "breakpoint_id", breakpointId, "suspend_policy", policy,
                    "observed_at", observedAt.toString(), "expires_at", expiresAt.toString());
        }
    }
}
