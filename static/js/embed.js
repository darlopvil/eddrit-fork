// Embed view: play DASH videos (most v.redd.it videos) on the bare <video>
// elements of templates/embed.html. Only loaded when the post has one.
// Without JavaScript the video keeps its poster and a link to the full post.
for (const video of document.querySelectorAll("video[data-dash]")) {
	const player = dashjs.MediaPlayer().create();
	player.initialize(video, video.dataset.dash, video.hasAttribute("autoplay"));
}
