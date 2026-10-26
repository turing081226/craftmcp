package io.craftmcp.debugger;

import com.google.gson.JsonObject;

final class Fault extends RuntimeException {
    final String code;
    final JsonObject details;

    Fault(String code, String message) { this(code, message, Json.object()); }

    Fault(String code, String message, JsonObject details) {
        super(message);
        this.code = code;
        this.details = details;
    }

    JsonObject json() { return Json.object("code", code, "message", getMessage(), "details", details); }
}
