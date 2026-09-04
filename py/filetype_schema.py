"""filetype_schema -- file extension -> canonical Python / C / Rust type mapping."""
from __future__ import annotations
import os, re
__all__ = ["infer_schema","infer_file","infer_schema_c","infer_schema_rs","TYPES"]

TYPES: dict[str, dict[str,str]] = {}
_ORDER: list[str] = []

def _r(exts, py, c, rs, desc):
    for e in exts:
        e = e.lower().lstrip(".")
        if e not in TYPES: TYPES[e] = {}; _ORDER.append(e)
        TYPES[e].update({"python":py,"c":c,"rust":rs,"desc":desc})

_r(("json",),       "dict|list|pydantic.BaseModel|TypedDict","cJSON (jsmn)|json_object","serde_json::Value","JSON -- key-value trees, arrays")
_r(("yaml","yml"),  "dict|list|dataclass|pydantic.BaseModel",        "libyaml",         "serde_yaml::Value","YAML -- config, CI/CD, K8s")
_r(("toml",),       "tomli.loads()|dataclass|pydantic.BaseModel",   "toml lib",        "toml::Value|serde","TOML -- Cargo.toml, pyproject.toml")
_r(("csv",),        "list[dict]|csv.DictReader|pandas.DataFrame",  "fgetcsv->struct", "csv::Reader",     "CSV -- tabular data")
_r(("tsv",),        "list[dict]|csv.DictReader(sep=chr(9))",     "fgetcsv (tab)",   "csv::Reader",     "TSV -- tab-separated values")
_r(("xml",),        "xml.etree.ElementTree|lxml.etree","libxml2|rapidxml","quick-xml",     "XML -- config, SOAP, RSS, SVG")
_r(("json5",),      "json5.loads()|pyjson5|dataclass",    "json5 C lib",     "serde_json5",    "JSON5 -- JSON with comments")
_r(("msgpack",),    "msgpack.unpackb()|dataclass",       "msgpack_unpack",  "rmp-serde",      "MessagePack -- binary JSON alt")
_r(("cbor",),       "cbor2.loads()|dict",               "libcbor",         "ciborium::Value|serde","CBOR -- Concise Binary Object Representation")
_r(("ubjson",),     "ubjson.loads()|dict",              "libubjson",        "serde_ubjson",  "UBJSON -- binary JSON with type hints")
_r(("pickle","pkl"),"pickle.load()|cloudpickle|joblib",  "N/A",              "N/A",            "Pickle -- Python obj serialization (UNSAFE for untrusted)")

_r(("sql",),         "sqlite3.Row|sqlalchemy.Result|pandas.DataFrame","sqlite3_exec->struct","rusqlite|sqlx","SQLite dump/migration")
_r(("db","sqlite3"),"sqlite3.connect()|duckdb",         "sqlite3 open",     "rusqlite|Connection","SQLite binary database")
_r(("parquet",),    "pandas.DataFrame|pyarrow.ParquetFile|polars.DataFrame","libparquet (arrow)","arrow2::ParquetFile|polars","Apache Parquet -- columnar analytic storage")
_r(("duckdb","ddb"),"duckdb.connect()|pandas.DataFrame","N/A",               "duckdb-rs|Connection","DuckDB -- embedded OLAP database")

_r(("png",),         "PIL.Image|np.ndarray|wand.image",   "stb_image|libpng|lodepng",   "image crate|png crate","PNG -- lossless raster")
_r(("jpg","jpeg"),   "PIL.Image|np.ndarray",              "stb_image|libjpeg|mozjpeg",   "image crate|jpg crate","JPEG -- lossy photo")
_r(("gif",),         "PIL.Image|imageio|np.ndarray",      "stb_image|libgif",            "image crate|gif crate","GIF -- animated/static bitmap")
_r(("webp",),        "PIL.Image|pillow-av|opencv-python", "libwebp",                    "image crate|webp crate","WebP -- lossy/lossless+animation")
_r(("svg",),         "lxml.etree|svgwrite.Drawing",       "libxml2|nanosvg",             "resvg|usvg","SVG -- vector graphics XML")
_r(("mp4","mov","avi","mkv","webm"),"cv2.VideoCapture|decord|imageio_reader","ffmpeg libavcodec","ffmpeg-next|nokhwa","Video -- motion pictures")
_r(("mp3","wav","flac","ogg","aac","m4a"),"librosa.load()|soundfile|pydub","libavcodec|minimp3|dr_wav","rodio|symphonia|kira","Audio -- music/sound effects")
_r(("pdf",),         "pypdf.PdfReader|pdfplumber|fitz","mupdf (fitz)|poppler",          "lopdf|pdf crate","PDF -- portable document")

_r(("py",),          "class|def|@dataclass|TypedDict|Protocol|NamedTuple","N/A","N/A","Python source -- classes, functions, decorators")
_r(("pyx","pxd"),   "Cython def/cdef|nogil|fused types", "Cython C output", "N/A","Cython -- Python+C hybrid source")

_r(("c","h"),       "struct|union|enum|typedef|extern|#define","struct|union|enum|typedef|#define","repr(transparent)|c_char|extern C","C source -- structs, enums, function signatures")
_r(("cpp","cc","cxx","hpp","hh","hxx"),"class|template|virtual|namespace|std::","class|template|virtual|std::|RAII","struct|impl|trait|std::","C++ source -- classes, templates, STL")

_r(("rs",),          "struct|enum|impl|pub|fn|trait|macro_rules!","extern C+bindgen->.h","struct|enum|impl|fn|trait|macro_rules!","Rust source -- structs, enums, traits, async fn")

_r(("js",),          "class|function|async|const/let|require()|module.exports","N/A","N/A","JavaScript source")
_r(("ts","tsx"),     "interface|type|class|async|generics|<T>","N/A","N/A","TypeScript -- interfaces, types, generics")
_r(("mjs","cjs"),   "ESM (import/export)|CommonJS (require/module.exports)","N/A","N/A","JS module variants")

_r(("go",),          "struct|func|interface{}|type|goroutine|defer","struct|func|interface|goroutine","struct|fn|trait|async","Go source -- structs, interfaces, goroutines")

_r(("java",),        "class|interface|@Override|throws|generics<T>","JNI|javac+native libs","jni crate (unsafe)","Java source -- classes, interfaces, annotations")
_r(("kt","kts"),    "class|data class|suspend|inline|sealed class","Kotlin->JVM bytecode","N/A","Kotlin -- data classes, coroutines")

_r(("env",),         "dict[str,str]|os.environ|pydantic BaseSettings","getenv()|char **envp","std::env|struct Config","Environment -- key=value pairs")
_r(("ini","cfg"),   "configparser.ConfigParser","GetPrivateProfileString","config crate|serde_ini","INI config -- Windows/Python ConfigParser")
_r(("properties",), "java.util.Properties|jproperties","Properties (Java native)","config|dotenvy","Java .properties -- key=value with escapes")
_r(("hcl","tfvars"),"python-hcl2|hcl2.loads()","HashiCorp HCL C lib","HCLEdit|rust-hcl2","HCL -- Terraform, Consul, Nomad")

_r(("nix",),         "nix.eval|nix-instantiate|builtins.fetchTree","N/A","N/A","Nix expression -- functional package/OS config")
_r(("bazel","bzl"), "build.bazel|@rule|native.genrule","N/A","N/A","Starlark/Bazel BUILD files")
_r(("cmake",),       "cmake --build .|add_library|target_link_libraries","CMakeLists.txt","CMakeLists.txt","CMake -- C/C++ build configuration")

_r(("pem","crt","cer"),"cryptography.x509|ssl|OpenSSL.crypto","OpenSSL X509|mbedtls","rustls|native_tls|rcgen","PEM-encoded X.509 certificate")
_r(("key",),         "cryptography RSAPrivateKey|Ed25519PrivateKey","OpenSSL EVP_PKEY|mbedtls|libsodium","ring|ed25519_dalek|rcgen","Private key (PEM-encoded RSA/Ed25519/ECDSA)")
_r(("p12","pfx","jks"),"cryptography import pkcs12|keytool","OpenSSL PKCS12|NSS","pkcs12|rcgen","PKCS#12/JKS -- bundled cert+key")
_r(("protobin","pb","pb2"),"google.protobuf.message.SerializeToString","protobuf-lite|nanopb","prost encode","Binary protobuf payload (not .proto schema)")

_r(("tar","tgz","tbz2","txz"),"tarfile.open()|tar|archive lib","libarchive|tar.h","tar crate|flate2","Tape archive -- Unix tar with optional compression")
_r(("zip",),         "zipfile.ZipFile|shutil|archivemount","libzip|miniz|zlib","zip crate","ZIP -- pkzip, OOXML, JAR")

_EXT_RE = re.compile(r"\.([a-zA-Z0-9]+)(?:\.\w+)*$")

def infer_schema(text: str) -> list[str]:
    """Return type hints from a file path or extension string."""
    hits = []
    for m in _EXT_RE.finditer(text):
        ext = m.group(1).lower()
        if ext in TYPES:
            t = TYPES[ext]
            hits.append(f"{ext} -> python: {t['python']} | c: {t['c']} | rust: {t['rust']}")
    return hits if hits else [f"{text}: no schema mapping"]

def infer_file(path: str) -> dict:
    base = os.path.basename(path)
    for m in _EXT_RE.finditer(base):
        ext = m.group(1).lower()
        if ext in TYPES:
            return dict(TYPES[ext], ext=ext)
    return {"ext":"","python":"","c":"","rust":"","desc":"no mapping"}

def infer_schema_c(ext: str) -> str:
    return TYPES.get(ext.lstrip(".").lower(),{}).get("c","N/A") or "N/A"

def infer_schema_rs(ext: str) -> str:
    return TYPES.get(ext.lstrip(".").lower(),{}).get("rust","N/A") or "N/A"

if __name__ == "__main__":
    import sys
    for arg in sys.argv[2:]:
        print(f"\n=== {arg} ===")
        for h in infer_schema(arg): print(h)
        if os.path.exists(arg):
            d = infer_file(arg)
            if d.get("ext"): print(f"  desc: {d['desc']}")
