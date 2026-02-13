use anyhow::{Context, Result, anyhow};
use embedded_graphics::{
    mono_font::{ascii::FONT_10X20, MonoTextStyle},
    pixelcolor::Rgb888,
    prelude::*,
    primitives::{PrimitiveStyle, Triangle},
    text::Text,
};
use evdev::{Device, InputEventKind, Key};
use gstreamer::prelude::*;
use gstreamer_app::AppSink;
use serde::Deserialize;
use std::fs::{self, File};
use std::io::Read;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

// --- Configuration ---
#[derive(Debug, Deserialize, Clone)]
struct CameraConfig {
    name: String,
    url: String,
    #[serde(default)]
    comment: String,
}

// --- Constants ---
const AUTO_CYCLE_SECONDS: u64 = 1800; // 30 minutes
const FRAME_WIDTH: u32 = 800;
const FRAME_HEIGHT: u32 = 480;

// --- Framebuffer ---
struct Framebuffer {
    mem: memmap2::MmapMut,
    width: u32,
    height: u32,
    stride: u32,
}

impl Framebuffer {
    fn new(path: &str) -> Result<Self> {
        let file = File::options().read(true).write(true).open(path)
            .context("Failed to open framebuffer device")?;
        
        // In production, use `ioctl` (FBIOGET_VSCREENINFO) to populate these.
        // For Pi Touchscreen, 800x480 is standard.
        let width = FRAME_WIDTH;
        let height = FRAME_HEIGHT;
        let bpp = 32;
        let stride = width * (bpp / 8);
        
        let mem = unsafe { memmap2::MmapMut::map_mut(&file)? };

        Ok(Self { mem, width, height, stride })
    }
}

impl DrawTarget for Framebuffer {
    type Color = Rgb888;
    type Error = core::convert::Infallible;

    fn draw_iter<I>(&mut self, pixels: I) -> Result<(), Self::Error>
    where
        I: IntoIterator<Item = Pixel<Self::Color>>,
    {
        for Pixel(coord, color) in pixels.into_iter() {
            if coord.x >= 0 && coord.x < self.width as i32 && coord.y >= 0 && coord.y < self.height as i32 {
                let offset = (coord.y as usize * self.stride as usize) + (coord.x as usize * 4);
                if offset + 4 <= self.mem.len() {
                    // BGRA
                    self.mem[offset] = color.b;
                    self.mem[offset + 1] = color.g;
                    self.mem[offset + 2] = color.r;
                    self.mem[offset + 3] = 255;
                }
            }
        }
        Ok(())
    }
}

impl OriginDimensions for Framebuffer {
    fn size(&self) -> Size {
        Size::new(self.width, self.height)
    }
}

// --- Input Helper ---
fn spawn_input_thread(events: Arc<Mutex<Vec<bool>>>) {
    thread::spawn(move || {
        let mut device_path = None;
        // Simple discovery
        if let Ok(dir) = fs::read_dir("/dev/input") {
            for entry in dir.flatten() {
                if let Ok(dev) = Device::open(entry.path()) {
                    let name = dev.name().unwrap_or("").to_lowercase();
                    if name.contains("touch") || name.contains("ads7846") || name.contains("waveshare") {
                        println!("Input: Found {}", name);
                        device_path = Some(entry.path());
                        break;
                    }
                }
            }
        }

        if let Some(path) = device_path {
            if let Ok(mut dev) = Device::open(path) {
                loop {
                    // fetch_events blocks by default
                    if let Ok(iter) = dev.fetch_events() {
                        for ev in iter {
                            if ev.kind() == InputEventKind::Key && ev.code() == Key::BTN_TOUCH.code() && ev.value() == 0 {
                                // Touch Release -> Click
                                if let Ok(mut q) = events.lock() {
                                    q.push(true);
                                }
                            }
                        }
                    } else {
                        // Device error or disconnect
                        thread::sleep(Duration::from_secs(1));
                    }
                }
            }
        } else {
            eprintln!("Input: No touch device found.");
        }
    });
}

// --- Pipeline Helper ---
struct VideoPipeline {
    pipeline: gstreamer::Pipeline,
    sink: AppSink,
}

impl VideoPipeline {
    fn new(url: &str) -> Result<Self> {
        // Hardware accelerated decoding for Pi (v4l2h264dec)
        // Force BGRA for Framebuffer
        let pipeline_str = format!(
            "rtspsrc location={} latency=0 protocols=tcp ! rtph264depay ! h264parse ! v4l2h264dec ! videoconvert ! video/x-raw,format=BGRA,width={},height={} ! appsink name=sink drop=true max-buffers=1",
            url, FRAME_WIDTH, FRAME_HEIGHT
        );
        
        let pipeline = gstreamer::parse_launch(&pipeline_str)?
            .downcast::<gstreamer::Pipeline>()
            .map_err(|_| anyhow!("Not a pipeline"))?;

        let sink = pipeline.by_name("sink").context("No sink")?
            .downcast::<AppSink>()
            .map_err(|_| anyhow!("Not an AppSink"))?;

        pipeline.set_state(gstreamer::State::Playing)?;
        Ok(Self { pipeline, sink })
    }

    fn stop(&self) -> Result<()> {
        self.pipeline.set_state(gstreamer::State::Null)?;
        Ok(())
    }
}

// --- Main ---
fn main() -> Result<()> {
    gstreamer::init()?;

    let config_path = "feeds.json";
    let cameras: Vec<CameraConfig> = serde_json::from_reader(
        File::open(config_path).context("Could not open feeds.json")?
    )?;
    if cameras.is_empty() { return Err(anyhow!("No cameras defined")); }

    let mut fb = match Framebuffer::new("/dev/fb0") {
        Ok(fb) => fb,
        Err(e) => {
            eprintln!("Error opening FB: {}", e);
            // Panic or loop? simplified for now:
            return Err(e);
        }
    };

    let touch_queue = Arc::new(Mutex::new(Vec::new()));
    spawn_input_thread(touch_queue.clone());

    let running = Arc::new(Mutex::new(true));
    let r = running.clone();
    ctrlc::set_handler(move || { *r.lock().unwrap() = false; })?;

    let mut current_idx = 0;
    let mut pipeline_wrapper = VideoPipeline::new(&cameras[current_idx].url)?;
    let mut last_interaction = Instant::now();

    let name_style = MonoTextStyle::new(&FONT_10X20, Rgb888::WHITE);
    let tri_style = PrimitiveStyle::with_fill(Rgb888::new(200, 200, 200));

    println!("Starting loop for camera: {}", cameras[current_idx].name);

    while *running.lock().unwrap() {
        // 1. Check Video
        if let Some(sample) = pipeline_wrapper.sink.try_pull_sample(gstreamer::ClockTime::from_mseconds(10)) {
            let buffer = sample.buffer().context("No buffer")?;
            let map = buffer.map_readable()?;
            
            // Blit to FB
            let len = fb.mem.len().min(map.len());
            fb.mem[..len].copy_from_slice(&map[..len]);
            drop(map);

            // 2. Draw Overlay
            let name = &cameras[current_idx].name;
            // Center text roughly
            let text_width = name.len() as u32 * 10;
            let text_x = (FRAME_WIDTH - text_width) / 2;
            Text::new(name, Point::new(text_x as i32, 30), name_style).draw(&mut fb).ok();

            // Draw Arrows
            Triangle::new(Point::new(10, 240), Point::new(60, 210), Point::new(60, 270))
                .into_styled(tri_style).draw(&mut fb).ok();
            Triangle::new(Point::new(790, 240), Point::new(740, 210), Point::new(740, 270))
                .into_styled(tri_style).draw(&mut fb).ok();
        }

        // 3. Logic: Check for Click or Timeout
        let mut switch_cam = false;
        {
            let mut q = touch_queue.lock().unwrap();
            if !q.is_empty() {
                q.clear();
                switch_cam = true;
                last_interaction = Instant::now();
                println!("Touch detected!");
            }
        }

        if last_interaction.elapsed().as_secs() > AUTO_CYCLE_SECONDS {
            switch_cam = true;
            last_interaction = Instant::now();
            println!("Auto-cycling...");
        }

        if switch_cam {
            pipeline_wrapper.stop()?;
            current_idx = (current_idx + 1) % cameras.len();
            println!("Switching to: {}", cameras[current_idx].name);
            pipeline_wrapper = VideoPipeline::new(&cameras[current_idx].url)?;
        }
    }

    pipeline_wrapper.stop()?;
    Ok(())
}
