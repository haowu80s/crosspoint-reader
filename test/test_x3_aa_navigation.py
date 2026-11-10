"""Compile production refinement and pending-turn paths against a recording display."""

from pathlib import Path
import os
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "src/activities/reader/EpubReaderActivity.cpp").read_text()
header = (ROOT / "src/activities/reader/EpubReaderActivity.h").read_text()


def block(start):
    first = source.index(start)
    opening = source.index("{", first)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[first:end]


render = block("void EpubReaderActivity::renderContents(")
queue = block("if (turnGuardActive) {")
consume = block("if (pendingManualTurn != 0 && !turnGuardActive) {")
fields = "\n".join(re.search(pattern, header)[0] for pattern in (
    r"std::atomic<int8_t> pendingManualTurn\{0\};",
    r"bool pendingManualTurnTouch = false;",
))

harness = r"""
#include <algorithm>
#include <atomic>
#include <cassert>
#include <cstdint>
#include <functional>
#include <memory>
#include <string>
#include <vector>
#define LOG_DBG(...) ((void)0)
#define LOG_ERR(...) ((void)0)
unsigned long millis() { static unsigned long n=0; return ++n; }
template<class T> auto makeUniqueNoThrow(size_t n) { return std::make_unique<T>(n); }
struct HalDisplay {
  enum RefreshMode { FAST_REFRESH, HALF_REFRESH };
  enum class GrayscaleMode { Absolute, Overlay };
  enum class GrayscaleBase { Separate, Combined };
  enum class Controller { UC8279, Other };
  struct Caps {
    bool stripUploads=true, asyncBase=false;
    GrayscaleBase base=GrayscaleBase::Separate;
    bool supported() const { return false; }
  };
};
struct { HalDisplay::Controller getController() { return HalDisplay::Controller::Other; } } display;
struct { bool x3=true; bool deviceIsX3() { return x3; } } gpio;
struct BoardConfig { static bool isEegoA4() { return false; } };
struct { size_t getFreeHeap() { return 0; } size_t getMaxAllocHeap() { return 0; } } ESP;
struct CrossPointSettings { enum { IMAGES_DISPLAY_BW=0 }; };
struct {
  int imageRendering=1;
  bool textAntiAliasing=true;
  int getReaderFontId() { return 1; }
  int getRefreshFrequency() { return 10; }
} SETTINGS;
struct ImageBlock { static void clearRenderFailures() {} static void releaseRenderCache() {} };
struct FontCache {
  struct Scope { void endScanAndPrewarm() {} };
  Scope createPrewarmScope() { return {}; }
};
struct GfxRenderer {
  enum RenderMode { BW, GRAYSCALE_LSB, GRAYSCALE_MSB };
  RenderMode mode=BW;
  HalDisplay::Caps caps;
  FontCache cache;
  bool strip=false, dirty=false, readable=false;
  int uploads=0, gray=0, cleanups=0;
  std::vector<std::string> events;
  std::function<void(const std::string&)> hook;
  void event(const std::string& e) { events.push_back(e); if(hook) hook(e); }
  FontCache* getFontCacheManager() { return &cache; }
  HalDisplay::Caps grayscaleCapabilities(HalDisplay::GrayscaleMode=HalDisplay::GrayscaleMode::Overlay) { return caps; }
  void setDitherImagesInBw(bool) {}
  void displayBuffer(HalDisplay::RefreshMode) { assert(!strip); readable=true; event("bw"); }
  bool displayGrayscaleBase(HalDisplay::GrayscaleMode, HalDisplay::RefreshMode) { displayBuffer(HalDisplay::FAST_REFRESH); return true; }
  void displayGrayscaleBase(HalDisplay::RefreshMode) { displayBuffer(HalDisplay::FAST_REFRESH); }
  void preconditionGrayscale() { event("precondition"); }
  int getDisplayHeight() { return 160; }
  int getDisplayWidthBytes() { return 99; }
  void setRenderMode(RenderMode m) { assert(!strip); mode=m; event(m==BW?"mode-bw":m==GRAYSCALE_LSB?"mode-lsb":"mode-msb"); }
  void beginStripTarget(uint8_t*,int,int) { assert(!strip); strip=true; event("begin"); }
  void clearScreen(int=255) {}
  void endStripTarget() { assert(strip); strip=false; event("end"); }
  void waitRefreshComplete() { assert(!strip); }
  void writeGrayscalePlaneStrip(bool,const uint8_t*,int,int) { assert(!strip); dirty=true; ++uploads; event("upload"); }
  void displayGrayBuffer() {
    assert(!strip); ++gray; event("gray-start"); event("gray-complete");
  }
  void cleanupGrayscaleWithFrameBuffer() {
    assert(!strip && mode==BW); ++cleanups; dirty=false; event("cleanup");
  }
  bool storeBwBuffer() { return true; }
  void copyGrayscaleLsbBuffers() {}
  void copyGrayscaleMsbBuffers() {}
  void restoreBwBuffer() {}
};
struct Page {
  bool images=false;
  bool hasImages() { return images; }
  bool hasImagesNeedingDecode() { return false; }
  void render(GfxRenderer& r,int,int,int) { r.event(r.strip?(r.mode==GfxRenderer::GRAYSCALE_LSB?"render-lsb":"render-msb"):"render-bw"); }
  void renderImages(GfxRenderer& r,int f,int x,int y) { render(r,f,x,y); }
  void renderWithImagePlaceholders(GfxRenderer& r,int f,int x,int y) { render(r,f,x,y); }
};
namespace ReaderUtils {
void displayWithRefreshCycle(GfxRenderer& r,int&,bool=false) { r.displayBuffer(HalDisplay::FAST_REFRESH); }
void displayBaseWithRefreshCycle(GfxRenderer& r,int&) { r.displayBuffer(HalDisplay::FAST_REFRESH); }
}
namespace haptic_feedback { int calls=0; void touchAction() { ++calls; } }
struct EpubReaderActivity {
  GfxRenderer renderer;
  bool forcedRefreshPending=false, renderBusy=false, canTurn=true;
  int pagesUntilFullRefresh=10, turns=0, updates=0;
  bool forward=false;
  int sectionValue=1;
  int* section=&sectionValue;
  std::function<void()> duringTurn;
""" + fields + r"""
  void renderStatusBar() {}
  void drawClippingHighlights(Page&,int,int,int) {}
  bool pageTurn(bool next) {
    assert(!renderBusy);
    if(duringTurn) duringTurn();
    forward=next; ++turns; return canTurn;
  }
  void notePageTurn(bool,bool) {}
  void requestUpdate() { ++updates; }
  void queue(bool prevTriggered,bool fromTouch=false) {
    const bool turnGuardActive=true;
    struct { bool prev, next; } touch{fromTouch,false};
""" + queue + r"""
  }
  void consume() {
    const bool turnGuardActive=renderBusy;
""" + consume + r"""
  }
  void renderContents(std::unique_ptr<Page>,int,int,int,int);
  void run(bool images=false) {
    renderBusy=true;
    auto p=std::make_unique<Page>(); p->images=images;
    renderContents(std::move(p),0,0,0,0);
    renderBusy=false;
    assert(!renderer.strip && renderer.mode==GfxRenderer::BW);
  }
};
""" + render + r"""
int main() {
  // Normal completion and a request made before the render task starts.
  { EpubReaderActivity a; a.run(); assert(a.renderer.gray==1 && a.renderer.uploads==4 && a.renderer.cleanups==1); }
  { EpubReaderActivity a; a.queue(false); a.run(); assert(a.renderer.readable && a.renderer.gray==0 && a.renderer.uploads==0); assert(a.pendingManualTurn); }
  // Requests at safe boundaries must retain navigation and avoid partial activation.
  for(const auto& point: {"bw", "render-lsb", "render-msb", "mode-msb"}) {
    EpubReaderActivity a;
    bool fired=false;
    a.renderer.hook=[&](const std::string& e) { if(!fired && e==point) { fired=true; a.queue(false); } };
    a.run();
    assert(fired && a.renderer.readable && a.renderer.gray==0 && a.pendingManualTurn);
    assert(!a.renderer.dirty);
    assert(a.renderer.cleanups==(a.renderer.uploads?1:0));
    a.consume(); assert(a.turns==1 && a.forward && !a.pendingManualTurn);
    a.renderer.hook={}; a.run(); assert(a.renderer.gray==1);
  }
  // Cancel after each possible upload, including the final MSB upload.
  for(int at=1;at<=4;++at) {
    EpubReaderActivity a;
    a.renderer.hook=[&](const std::string& e) { if(e=="upload" && a.renderer.uploads==at) a.queue(true); };
    a.run();
    assert(a.renderer.uploads==at && a.renderer.gray==0 && a.renderer.cleanups==1 && !a.renderer.dirty);
    assert(a.pendingManualTurn && a.pendingManualTurn==-1);
  }
  // Physical activation completes; the next request is not consumed by rendering.
  { EpubReaderActivity a;
    a.renderer.hook=[&](const std::string& e) { if(e=="gray-start") a.queue(false); };
    a.run(); assert(a.renderer.gray==1 && a.renderer.cleanups==1 && a.pendingManualTurn);
    auto& e=a.renderer.events;
    assert(std::find(e.begin(),e.end(),"gray-complete") < std::find(e.begin(),e.end(),"cleanup"));
  }
  // The main-loop production snippets keep last-direction-wins and touch metadata.
  { EpubReaderActivity a; a.renderBusy=true;
    a.queue(false,true); a.queue(true); a.queue(false,true); a.consume(); assert(a.turns==0);
    a.renderBusy=false; a.consume(); a.consume();
    assert(a.turns==1 && a.forward && !a.pendingManualTurn && haptic_feedback::calls==1);
  }
  { EpubReaderActivity a; a.queue(false); a.section=nullptr; a.consume(); assert(!a.pendingManualTurn); }
  { EpubReaderActivity a; a.canTurn=false; a.queue(true,true); a.consume();
    assert(!a.pendingManualTurn && a.updates==1 && haptic_feedback::calls==1);
    a.run(); assert(a.renderer.gray==1);
  }
  // A render that starts while consuming the pending turn must still see it.
  { EpubReaderActivity a; a.queue(false);
    a.duringTurn=[&] { a.run(); assert(a.renderer.gray==0 && a.pendingManualTurn); };
    a.consume(); assert(!a.pendingManualTurn && a.updates==1);
    a.duringTurn={}; a.run(); assert(a.renderer.gray==1);
  }
  // Do not change image pages, non-X3 displays, disabled AA, or combined/async paths.
  for(int excluded=0;excluded<6;++excluded) {
    EpubReaderActivity a; a.queue(false);
    gpio.x3=excluded!=0;
    SETTINGS.textAntiAliasing=excluded!=1;
    SETTINGS.imageRendering=excluded==3?0:1;
    if(excluded==4) a.renderer.caps.base=HalDisplay::GrayscaleBase::Combined;
    if(excluded==5) a.renderer.caps.asyncBase=true;
    a.run(excluded==2 || excluded==3);
    assert(a.renderer.gray==(excluded==1?0:1));
    gpio.x3=true; SETTINGS.textAntiAliasing=true; SETTINGS.imageRendering=1;
  }
}
"""

with tempfile.TemporaryDirectory() as directory:
    cpp = Path(directory) / "refinement.cpp"
    executable = Path(directory) / "refinement"
    cpp.write_text(harness)
    flags = os.environ.get("X3_AA_TEST_FLAGS", "").split()
    subprocess.run(["c++", "-std=c++20", "-O1", "-Wall", "-Wextra",
                    "-Wno-unused-variable", "-Wno-unused-parameter", *flags,
                    str(cpp), "-o", str(executable)], check=True)
    subprocess.run([str(executable)], check=True)
print("X3 refinement cancellation and pending-navigation checks passed")
