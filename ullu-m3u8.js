import { chromium } from "playwright";
import fs from "fs";

const SOURCE_JSON =
  "https://hitmaal.in/ullu.json";

const OUTPUT = "ullu-m3u8.json";

/* 🔥 SPEED CONFIG */
const MAX_WORKERS = 8;
const WAIT_TIME = 3500;
const LIMIT = 200; // 👈 ONLY FETCH 200 VIDEOS

(async () => {
  const data = await (await fetch(SOURCE_JSON)).json();

  // ✅ TAKE ONLY FIRST 200
  const episodes = data.episodes.slice(0, LIMIT);

  console.log(`🎯 Fetching only ${episodes.length} videos`);

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();

  // 🚫 Block heavy resources
  await context.route("**/*", route => {
    const type = route.request().resourceType();
    if (["image", "font", "stylesheet"].includes(type)) {
      route.abort();
    } else {
      route.continue();
    }
  });

  let index = 0;
  const results = [];

  async function worker(id) {
    const page = await context.newPage();

    while (index < episodes.length) {
      const ep = episodes[index++];
      console.log(`👷 Worker ${id} → ${ep.title}`);

      try {
        await page.goto(ep.link, { timeout: 30000 });
        await page.waitForTimeout(WAIT_TIME);

        // 🔥 IMPROVED: Get ALL video sources including full URLs with query parameters
        const stream = await page.evaluate(() => {
          // Method 1: Check video elements
          const videoElement = document.querySelector("video");
          if (videoElement && videoElement.src) {
            return videoElement.src;
          }
          
          // Method 2: Check source elements inside video
          const sourceElement = document.querySelector("video source");
          if (sourceElement && sourceElement.src) {
            return sourceElement.src;
          }
          
          // Method 3: Search HTML for video URLs (full pattern including query params)
          const html = document.documentElement.innerHTML;
          
          // Match .m3u8 URLs with full query parameters
          const m3u8Match = html.match(/https?:\/\/[^\s"'<>]+\.m3u8[^\s"'<>]*/);
          if (m3u8Match) return m3u8Match[0];
          
          // Match .mp4 URLs with full query parameters (including ? and everything after)
          const mp4Match = html.match(/https?:\/\/[^\s"'<>]+\.mp4[^\s"'<>]*/);
          if (mp4Match) return mp4Match[0];
          
          // Method 4: Check for URLs in iframes or embedded players
          const allUrls = html.match(/https?:\/\/[^\s"'<>]+(?:\.m3u8|\.mp4)[^\s"'<>]*/g);
          if (allUrls && allUrls.length > 0) return allUrls[0];
          
          // Method 5: Check data attributes or JavaScript variables
          const scriptContent = Array.from(document.querySelectorAll("script"))
            .map(script => script.textContent)
            .join(" ");
          
          const scriptMatch = scriptContent.match(/(?:src|url|file|source)[\s]*:[\s]*["']([^"']+\.(?:m3u8|mp4)[^"']*)["']/i);
          if (scriptMatch) return scriptMatch[1];
          
          return null;
        });

        if (stream) {
          // Clean up the URL if needed (decode URI components)
          const cleanStream = decodeURIComponent(stream);
          
          results.push({
            title: ep.title,
            upload_time: ep.upload_time,
            duration: ep.duration,
            page_url: ep.link,
            thumbnail: ep.thumbnail || null, // ✅ Add thumbnail from source data
            stream_type: cleanStream.includes(".m3u8") ? "m3u8" : "mp4",
            stream_url: cleanStream // Full URL with all query parameters preserved
          });

          console.log(`✅ Found (${id}): ${cleanStream.substring(0, 100)}...`);
        } else {
          console.log(`❌ No stream found for ${ep.title}`);
        }
      } catch (e) {
        console.log(`⚠️ Error → ${ep.title}: ${e.message}`);
      }
    }

    await page.close();
  }

  // 🔥 Run workers
  await Promise.all(
    Array.from({ length: MAX_WORKERS }, (_, i) => worker(i + 1))
  );

  await browser.close();

  // 💾 Save output with full URLs and thumbnails
  fs.writeFileSync(
    OUTPUT,
    JSON.stringify(
      {
        created_at: new Date().toISOString(),
        total: results.length,
        videos: results
      },
      null,
      2
    )
  );

  console.log(`🎉 DONE → ${results.length} videos saved to ${OUTPUT}`);
})();
