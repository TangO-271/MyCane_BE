git checkout -b optimize-performance
git add .
git commit -m "Optimize API and pipeline performance"
git push origin optimize-performance
gh pr create --base main --head optimize-performance --title "Optimize API and pipeline performance" --body "Added ThreadPool in Pipeline and DB Pooling + PNG Caching in the API"
