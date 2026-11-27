"""One editorial correction. Preserve the original permalink and any later edits."""

import os
from html import unescape

import requests


TITLE = "Jumpertown and New Site will join Thrasher and Wheeler in the 2027–29 tennis region"
ORIGINAL_TITLE = "MHSAA tennis realignment will put all four Prentiss County schools in one region cycle"
ORIGINAL_MODIFIED = "2026-11-27T04:02:13"
SLUG = "mhsaa-tennis-realignment-will-put-all-four-prentiss-county-schools-in-one-region-cycle-41dec95fc359"
OLD_COUNT = ("The new alignment changes how often those county schools are grouped together. "
             "The release says the change will put six different pairings of the four schools inside one region, "
             "while currently only two of those six pairings are in the same region.")
NEW_COUNT = ("Comparing the two region lists shows that all six pairings of those four schools "
             "will be within one region. Today, just two are region pairings: Jumpertown and New Site, "
             "and Thrasher and Wheeler.")
TABLE = "https://championship-manager-seven.vercel.app/api/embed/e3ee83d5a06539cbe6c2e8416fd1c84d"
CURRENT = "https://www.misshsaa.com/2024/11/20/2025-27-tennis-regions/"
DRAW = "https://www.misshsaa.com/2026/11/11/2026-27-class-i-4a-team-tennis-bracket/"
ALT = "Jumpertown and New Site join Thrasher, Wheeler and Biggersville in Class I Region 1 for 2027–29."


def read(session, url, **params):
    reply = session.get(url, params=params, timeout=25)
    reply.raise_for_status()
    return reply.json()


def main():
    root = os.environ["WORDPRESS_BASE_URL"].rstrip("/")
    if root not in {"https://prentissnews.com", "https://www.prentissnews.com"}:
        raise RuntimeError("Unexpected Prentiss site")
    api = root + "/wp-json/wp/v2/"
    editor = requests.Session()
    editor.auth = (os.environ["WORDPRESS_USERNAME"], os.environ["WORDPRESS_APP_PASSWORD"])
    post_url, image_url = api + "posts/9034", api + "media/9033"
    post = read(editor, post_url, context="edit")
    assert (post["id"], post["status"], post["slug"], post["featured_media"]) == (9034, "publish", SLUG, 9033)
    body = post["content"]["raw"]
    if post["title"]["raw"] != TITLE:
        assert post["modified_gmt"] == ORIGINAL_MODIFIED and post["title"]["raw"] == ORIGINAL_TITLE
        assert body.count(OLD_COUNT) == 1 and body.count(TABLE) == 1 and CURRENT not in body and DRAW not in body
        anchor = 'Mississippi High School Activities Association</a>'
        assert body.count(anchor) == 1
        links = (anchor + ', <a href="' + CURRENT + '" rel="noopener">current regions</a> and '
                 '<a href="' + DRAW + '" rel="noopener">spring 2027 playoff draw</a>')
        body = body.replace(OLD_COUNT, NEW_COUNT).replace(anchor, links)
        editor.post(post_url, json={"title": TITLE, "content": body}, timeout=25).raise_for_status()
    assert NEW_COUNT in body and CURRENT in body and DRAW in body
    public = read(requests, post_url)
    assert public["link"] == post["link"] and public["status"] == "publish" and public["featured_media"] == 9033
    assert unescape(public["title"]["rendered"]) == TITLE and NEW_COUNT in public["content"]["rendered"]
    media = read(editor, image_url, context="edit")
    assert media["source_url"].endswith("/prentiss-four-schools-tennis-regions-2027-29.png")
    if media["alt_text"] == "":
        editor.post(image_url, json={"alt_text": ALT}, timeout=25).raise_for_status()
        assert read(requests, image_url)["alt_text"] == ALT
    page = requests.get(public["link"], timeout=25)
    page.raise_for_status()
    assert TITLE in unescape(page.text) and NEW_COUNT in page.text and DRAW in page.text
    print("Verified the corrected headline and the two assignment lists on published post 9034.")


if __name__ == "__main__":
    main()
