// ================================================================
//  CpiRunner.groovy  —  SAP CPI Groovy Script Simulator Harness
//  Usage: groovy CpiRunner.groovy <input.json> <output.json>
//
//  Strategy:
//    1. The outer script (this file) runs directly in the Groovy CLI.
//    2. It injects a CPIMessage class (NOT named "Message" to avoid
//       conflicts with user imports) into a GroovyShell binding.
//    3. The harness renames it to "Message" via binding alias so
//       the user's  message.getBody() / setHeader() etc. all work.
//    4. println inside the user script is captured via metaClass.
// ================================================================
import groovy.json.JsonSlurper
import groovy.json.JsonOutput

// ── 1. Read runner input ─────────────────────────────────────────
def inp        = new JsonSlurper().parse(new File(args[0]))
def outFile    = new File(args[1])
String uScript = inp.script   ?: ""
String fnName  = (inp.function ?: "processData").trim()
String bodyTxt = inp.body     ?: ""
def   inHdrs   = inp.headers    ?: [:]
def   inProps  = inp.properties ?: [:]

// ── 2. Mock Message class (defined in OUTER scope, no generics issue) ──
class CPIMessage {
    def _body   = ""
    def _hdrs   = [:]
    def _props  = [:]
    def _logP   = [:]
    def _custH  = [:]

    CPIMessage(String b, Map h, Map p) {
        _body = b ?: ""
        h?.each { k, v -> _hdrs[k?.toString() ?: ""] = v?.toString() ?: "" }
        p?.each { k, v -> _props[k?.toString() ?: ""] = v?.toString() ?: "" }
    }

    // Body — accepts any type hint (String, String.class, etc.)
    def  getBody(def t = null) { _body }
    void setBody(def v)        { _body = v?.toString() ?: "" }

    // Headers
    def  getHeaders()              { new HashMap(_hdrs) }
    void setHeader(String k, def v){ _hdrs[k] = v?.toString() ?: "" }
    def  getHeader(String k)       { _hdrs[k] }

    // Properties
    def  getProperties()              { new HashMap(_props) }
    void setProperty(String k, def v) { _props[k] = v?.toString() ?: "" }
    def  getProperty(String k)        { _props[k] }

    // MPL
    void setAdapterMessageProperty(String k, def v) { _logP[k]  = v?.toString() ?: "" }
    void addCustomHeaderProperty  (String k, def v) { _custH[k] = v?.toString() ?: "" }

    // Snapshot for JSON output
    def snapshot() {[
        body                  : _body,
        headers               : new HashMap(_hdrs),
        properties            : new HashMap(_props),
        logProperties         : new HashMap(_logP),
        customHeaderProperties: new HashMap(_custH)
    ]}
}

// ── 3. Console capture ───────────────────────────────────────────
def consoleLinesRef = []

// ── 4. Build result container ────────────────────────────────────
def result = [success: false, output: [:], console: [], error: ""]

try {
    def msgObj = new CPIMessage(bodyTxt, inHdrs as Map, inProps as Map)

    // ── 5. Build the script that runs inside GroovyShell ─────────
    //       Key decisions:
    //       • Strip  import com.sap.gateway...Message  — we inject it
    //       • Strip  import groovy.xml.*  — already on classpath; add it explicitly
    //       • NO generics (Map<K,V>) inside the shell-compiled string
    //       • The harness string only wires up the call; user code is verbatim
    // ─────────────────────────────────────────────────────────────

    // Remove SAP import lines (we provide Message ourselves)
    // and re-add groovy.xml / groovy.json so XmlSlurper etc. work
    String cleanScript = uScript
        .replaceAll(/(?m)^\s*import\s+com\.sap\.[^\n]*\n?/, "")
        .replaceAll(/(?m)^\s*import\s+com\.sap\.[^\n]*$/, "")

    // Build the full shell script
    // IMPORTANT: no Map<X,Y> here — use plain 'def' or 'Map'
    String shellScript = """
import groovy.xml.*
import groovy.json.*
import groovy.xml.XmlSlurper
import groovy.xml.XmlUtil

// ── User script (verbatim, SAP imports stripped) ──────────────────
${cleanScript}
// ─────────────────────────────────────────────────────────────────

// Invoke the requested function with the injected message object
def __finalMsg = ${fnName}(__cpiMsg)
if (__finalMsg == null) __finalMsg = __cpiMsg
__finalMsg
"""

    // ── 6. Set up GroovyShell binding ────────────────────────────
    def binding = new Binding()
    binding.setVariable("__cpiMsg", msgObj)

    def shell  = new GroovyShell(this.class.classLoader, binding)
    def script = shell.parse(shellScript)

    // ── 7. Capture println via metaClass ─────────────────────────
    script.metaClass.println = { Object o ->
        consoleLinesRef << (o?.toString() ?: "null")
    }
    script.metaClass.println = { ->
        consoleLinesRef << ""
    }
    script.metaClass.print = { Object o ->
        consoleLinesRef << (o?.toString() ?: "")
    }

    // ── 8. Run ───────────────────────────────────────────────────
    def ret = script.run()

    def outMsg = (ret instanceof CPIMessage) ? ret : msgObj
    result.output  = outMsg.snapshot()
    result.console = consoleLinesRef
    result.success = true

} catch (groovy.lang.MissingMethodException e) {
    result.error   = "Function '${fnName}' not found in script or wrong argument count.\n${e.message}"
    result.console = consoleLinesRef

} catch (org.codehaus.groovy.control.MultipleCompilationErrorsException e) {
    // Parse/compile error — extract just the useful part
    String msg = e.message ?: ""
    // Groovy compile errors reference Script1.groovy line numbers
    // The harness preamble is ~10 lines, so adjust user line numbers
    def lines = msg.readLines()
    def clean = lines.findAll { it && !it.startsWith("\tat ") }
    result.error   = clean.join("\n").trim()
    result.console = consoleLinesRef

} catch (Exception e) {
    StringWriter sw = new StringWriter()
    e.printStackTrace(new PrintWriter(sw))
    def lines = sw.toString().readLines()
    // Keep error message + lines that reference the user script area
    def clean = [e.class.simpleName + ": " + (e.message ?: "")]
    clean += lines.findAll { l ->
        (l.contains("Script1.groovy") || l.contains("script_")) &&
        !l.contains("org.codehaus.groovy.runtime") &&
        !l.contains("sun.reflect") &&
        !l.contains("java.lang.reflect")
    }
    result.error   = clean.join("\n").trim()
    result.console = consoleLinesRef
}

// ── 9. Write output JSON ─────────────────────────────────────────
outFile.text = JsonOutput.prettyPrint(JsonOutput.toJson(result))
